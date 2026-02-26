from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_ecr as ecr,
    CfnOutput,
    aws_ssm as ssm,
)
from constructs import Construct


class EcrStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.frontend_repo = ecr.Repository(
            self,
            "FrontendRepo",
            repository_name="modernizer-frontend",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 5 images",
                    max_image_count=5,
                )
            ],
        )

        self.backend_repo = ecr.Repository(
            self,
            "BackendRepo",
            repository_name="modernizer-backend",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 5 images",
                    max_image_count=5,
                )
            ],
        )

        # Store URIs in SSM so the update script can look them up
        ssm.StringParameter(
            self,
            "FrontendEcrUriParam",
            parameter_name="/modernizer/frontend-ecr-uri",
            string_value=self.frontend_repo.repository_uri,
        )
        ssm.StringParameter(
            self,
            "BackendEcrUriParam",
            parameter_name="/modernizer/backend-ecr-uri",
            string_value=self.backend_repo.repository_uri,
        )

        CfnOutput(self, "FrontendRepoUri", value=self.frontend_repo.repository_uri)
        CfnOutput(self, "BackendRepoUri", value=self.backend_repo.repository_uri)
