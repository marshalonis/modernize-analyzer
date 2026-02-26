from aws_cdk import (
    Stack,
    Duration,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_ecr as ecr,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_ssm as ssm,
    CfnOutput,
)
from constructs import Construct


class EcsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        frontend_repo: ecr.Repository,
        backend_repo: ecr.Repository,
        frontend_alb_sg: ec2.SecurityGroup,
        frontend_task_sg: ec2.SecurityGroup,
        backend_alb_sg: ec2.SecurityGroup,
        backend_task_sg: ec2.SecurityGroup,
        default_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(
            self,
            "ModernizerCluster",
            vpc=vpc,
            cluster_name="modernizer",
            container_insights=True,
        )

        # Store cluster name for update script
        ssm.StringParameter(
            self,
            "ClusterNameParam",
            parameter_name="/modernizer/cluster-name",
            string_value=cluster.cluster_name,
        )

        # ------------------------------------------------------------------ #
        # IAM — backend task role (needs Bedrock + Bedrock:InvokeModel)       #
        # ------------------------------------------------------------------ #

        backend_task_role = iam.Role(
            self,
            "BackendTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Allows modernizer backend to call Bedrock",
        )
        backend_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],  # Narrow to specific model ARNs if desired
            )
        )

        # ------------------------------------------------------------------ #
        # Log groups                                                           #
        # ------------------------------------------------------------------ #

        backend_log_group = logs.LogGroup(
            self,
            "BackendLogGroup",
            log_group_name="/ecs/modernizer-backend",
            retention=logs.RetentionDays.ONE_WEEK,
        )
        frontend_log_group = logs.LogGroup(
            self,
            "FrontendLogGroup",
            log_group_name="/ecs/modernizer-frontend",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # ------------------------------------------------------------------ #
        # Backend — internal ALB + Fargate service                            #
        # ------------------------------------------------------------------ #

        backend_task_def = ecs.FargateTaskDefinition(
            self,
            "BackendTaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=backend_task_role,
        )
        backend_task_def.add_container(
            "BackendContainer",
            image=ecs.ContainerImage.from_ecr_repository(backend_repo, tag="latest"),
            port_mappings=[ecs.PortMapping(container_port=8000)],
            environment={
                "DEFAULT_MODEL_ID": default_model_id,
                "AWS_REGION": self.region,
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="backend",
                log_group=backend_log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        backend_alb = elbv2.ApplicationLoadBalancer(
            self,
            "BackendAlb",
            vpc=vpc,
            internet_facing=False,
            security_group=backend_alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )
        backend_listener = backend_alb.add_listener(
            "BackendListener",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        backend_service = ecs.FargateService(
            self,
            "BackendService",
            cluster=cluster,
            task_definition=backend_task_def,
            service_name="modernizer-backend",
            desired_count=1,
            security_groups=[backend_task_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            assign_public_ip=False,
        )
        backend_listener.add_targets(
            "BackendTargets",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[backend_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(30),
        )

        backend_url = f"http://{backend_alb.load_balancer_dns_name}:8000"

        # Store backend URL for frontend env var
        ssm.StringParameter(
            self,
            "BackendUrlParam",
            parameter_name="/modernizer/backend-url",
            string_value=backend_url,
        )

        # ------------------------------------------------------------------ #
        # Frontend — public ALB + Fargate service                             #
        # ------------------------------------------------------------------ #

        frontend_task_def = ecs.FargateTaskDefinition(
            self,
            "FrontendTaskDef",
            cpu=512,
            memory_limit_mib=1024,
        )
        frontend_task_def.add_container(
            "FrontendContainer",
            image=ecs.ContainerImage.from_ecr_repository(frontend_repo, tag="latest"),
            port_mappings=[ecs.PortMapping(container_port=8501)],
            environment={
                "BACKEND_URL": backend_url,
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="frontend",
                log_group=frontend_log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8501/_stcore/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(90),
            ),
        )

        frontend_alb = elbv2.ApplicationLoadBalancer(
            self,
            "FrontendAlb",
            vpc=vpc,
            internet_facing=True,
            security_group=frontend_alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )
        frontend_listener = frontend_alb.add_listener(
            "FrontendListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        frontend_service = ecs.FargateService(
            self,
            "FrontendService",
            cluster=cluster,
            task_definition=frontend_task_def,
            service_name="modernizer-frontend",
            desired_count=1,
            security_groups=[frontend_task_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            assign_public_ip=False,
        )
        frontend_listener.add_targets(
            "FrontendTargets",
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[frontend_service],
            health_check=elbv2.HealthCheck(
                path="/_stcore/health",
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(30),
            stickiness_cookie_duration=Duration.hours(1),
        )

        # Store service names for update script
        ssm.StringParameter(
            self,
            "FrontendServiceParam",
            parameter_name="/modernizer/frontend-service",
            string_value=frontend_service.service_name,
        )
        ssm.StringParameter(
            self,
            "BackendServiceParam",
            parameter_name="/modernizer/backend-service",
            string_value=backend_service.service_name,
        )

        # ------------------------------------------------------------------ #
        # Outputs                                                              #
        # ------------------------------------------------------------------ #

        CfnOutput(
            self,
            "FrontendUrl",
            value=f"http://{frontend_alb.load_balancer_dns_name}",
            description="Public URL for the Modernization Analyzer UI",
        )
        CfnOutput(self, "BackendUrl", value=backend_url)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
