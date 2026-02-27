import xml.etree.ElementTree as ET
from pathlib import Path
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    CfnOutput,
)
from constructs import Construct


def _load_allowed_cidrs() -> list[tuple[str, str]]:
    """
    Parse cdk/config.xml and return a list of (cidr, description) tuples
    for the frontend ALB whitelist.
    """
    config_path = Path(__file__).parent.parent / "config.xml"
    tree = ET.parse(config_path)
    root = tree.getroot()
    cidrs = []
    for entry in root.findall("./frontend_alb/allowed_cidrs/cidr"):
        cidr = entry.text.strip() if entry.text else ""
        description = entry.attrib.get("description", cidr)
        if cidr:
            cidrs.append((cidr, description))
    if not cidrs:
        raise ValueError("config.xml has no <cidr> entries under <frontend_alb><allowed_cidrs>")
    return cidrs


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

        # Security group: public ALB (frontend) — restricted to whitelisted CIDRs
        self.frontend_alb_sg = ec2.SecurityGroup(
            self,
            "FrontendAlbSg",
            vpc=self.vpc,
            description="Frontend ALB — allow HTTPS/HTTP from whitelisted CIDRs",
            allow_all_outbound=True,
        )
        for cidr, description in _load_allowed_cidrs():
            peer = ec2.Peer.ipv4(cidr)
            self.frontend_alb_sg.add_ingress_rule(peer, ec2.Port.tcp(80), description)
            self.frontend_alb_sg.add_ingress_rule(peer, ec2.Port.tcp(443), description)

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
