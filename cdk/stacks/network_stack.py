from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    CfnOutput,
)
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "ModernizerVpc",
            max_azs=2,
            nat_gateways=1,  # 1 NAT GW is sufficient and cost-effective for this use case
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # Security group: public ALB (frontend)
        self.frontend_alb_sg = ec2.SecurityGroup(
            self,
            "FrontendAlbSg",
            vpc=self.vpc,
            description="Frontend ALB — allow HTTPS/HTTP from internet",
            allow_all_outbound=True,
        )
        self.frontend_alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))
        self.frontend_alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))

        # Security group: Streamlit ECS tasks
        self.frontend_task_sg = ec2.SecurityGroup(
            self,
            "FrontendTaskSg",
            vpc=self.vpc,
            description="Streamlit ECS tasks",
            allow_all_outbound=True,
        )
        self.frontend_task_sg.add_ingress_rule(self.frontend_alb_sg, ec2.Port.tcp(8501))

        # Security group: internal ALB (backend)
        self.backend_alb_sg = ec2.SecurityGroup(
            self,
            "BackendAlbSg",
            vpc=self.vpc,
            description="Backend internal ALB — allow from frontend tasks",
            allow_all_outbound=True,
        )
        self.backend_alb_sg.add_ingress_rule(self.frontend_task_sg, ec2.Port.tcp(8000))

        # Security group: FastAPI ECS tasks
        self.backend_task_sg = ec2.SecurityGroup(
            self,
            "BackendTaskSg",
            vpc=self.vpc,
            description="FastAPI ECS tasks",
            allow_all_outbound=True,
        )
        self.backend_task_sg.add_ingress_rule(self.backend_alb_sg, ec2.Port.tcp(8000))

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
