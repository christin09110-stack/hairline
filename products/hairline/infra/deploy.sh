#!/usr/bin/env bash
# Deploy Hairline's always-on demo endpoint to a Graviton EC2 instance.
#
#   products/hairline/infra/deploy.sh [--type c8g.large] [--name hairline-demo]
#
# A public demo endpoint needs 80 and 443 for automatic TLS, and an instance role so
# the box can pull its own image from ECR at boot. Benchmark instances are launched
# separately by products/hairline/bench/instances.sh.
#
# Idempotent: re-running reuses the role, the security group and any instance
# already carrying the Name tag, and never launches a second one.
#
# Tear down with:  infra/graviton-teardown.sh <instance-id>

set -euo pipefail
export AWS_PAGER=""

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TYPE="c8g.large"
NAME="hairline-demo"
KEY_NAME="opencv26-hairline"
VOLUME_GB=30
TAG=""
UBUNTU_SSM="/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
ROLE="opencv26-instance-ecr-read"
SG_NAME="opencv26-hairline-sg"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)   TYPE="$2"; shift 2 ;;
    --name)   NAME="$2"; shift 2 ;;
    --key)    KEY_NAME="$2"; shift 2 ;;
    --tag)    TAG="$2"; shift 2 ;;
    --status) STATUS_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[34m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn(){ printf '\033[33mwarn\033[0m %s\n' "$*"; }

REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/opencv26/hairline"
if [[ -z "$TAG" ]]; then
  # buildx writes a `buildcache` image alongside the real one, and it is usually
  # the most recently pushed of the two. Picking it deploys a cache manifest that
  # has no entrypoint, so exclude it by name rather than trusting the timestamp.
  TAG="$(aws ecr describe-images --repository-name opencv26/hairline --region "$REGION" \
    --query "reverse(sort_by(imageDetails,&imagePushedAt))[?imageTags].{t:imageTags[0],d:imagePushedAt}" \
    --output text | awk '$2 != "buildcache" && $1 != "buildcache" {print $2; exit}')"
  [[ -z "$TAG" || "$TAG" == "buildcache" ]] && TAG="$(aws ecr describe-images \
    --repository-name opencv26/hairline --region "$REGION" \
    --query 'imageDetails[].imageTags[]' --output text | tr "\t" "\n" | grep -v buildcache | head -1)"
fi
[[ "$TAG" != "None" && -n "$TAG" ]] || { echo "no image in ECR; run infra/ecr.sh first" >&2; exit 1; }
say "image $REPO:$TAG"

# ---- instance role: read-only ECR, and SSM so we never need the ssh key -----

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  say "creating IAM role $ROLE"
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --tags Key=Project,Value=opencv26 >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  sleep 12   # IAM is eventually consistent and run-instances will reject it otherwise
  ok "created"
else
  ok "role $ROLE exists"
fi

# ---- security group ---------------------------------------------------------

VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region "$REGION")"
SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)"

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
  say "creating security group $SG_NAME"
  SG_ID="$(aws ec2 create-security-group --region "$REGION" \
    --group-name "$SG_NAME" --vpc-id "$VPC_ID" \
    --description "Hairline demo endpoint: 80 and 443 only" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=opencv26},{Key=Product,Value=hairline}]" \
    --query GroupId --output text)"
fi
for port in 80 443; do
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=$port,ToPort=$port,IpRanges=[{CidrIp=0.0.0.0/0,Description=public demo}]" \
    >/dev/null 2>&1 && ok "opened $port" || true
done
# Port 22 is deliberately NOT opened. The instance carries the SSM role, so a
# shell is `aws ssm start-session`, which needs no inbound port at all.
ok "security group $SG_ID"

# ---- existing instance? -----------------------------------------------------

EXISTING="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=tag:Project,Values=opencv26" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo None)"

if [[ "${STATUS_ONLY:-0}" == "1" ]]; then
  [[ "$EXISTING" == "None" ]] && { echo "no instance named $NAME"; exit 0; }
  aws ec2 describe-instances --region "$REGION" --instance-ids "$EXISTING" \
    --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name,PublicIpAddress,LaunchTime]' \
    --output text
  exit 0
fi

AMI="$(aws ssm get-parameter --region "$REGION" --name "$UBUNTU_SSM" \
  --query Parameter.Value --output text)"
say "Ubuntu 24.04 arm64 $AMI"

USER_DATA="$(mktemp)"
cat > "$USER_DATA" <<CLOUDINIT
#!/bin/bash
set -x
exec > >(tee /var/log/hairline-boot.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https

# Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \$VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list

# Caddy, for automatic TLS
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list

apt-get update
# One package per call. A single apt-get with five names aborts the whole
# transaction if any one of them is unavailable, which on the first attempt left
# the box with neither Docker nor Caddy and no obvious reason why.
apt-get install -y docker-ce docker-ce-cli containerd.io
apt-get install -y caddy
apt-get install -y unzip
systemctl enable --now docker

# Ubuntu 24.04 has no usable `awscli` package on arm64 here, so take the official
# v2 installer. Without it the instance cannot authenticate to ECR at boot.
cd /tmp
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip -o awscliv2.zip
unzip -oq awscliv2.zip
./aws/install --update
export PATH=/usr/local/bin:\$PATH

# The instance profile is what authorises this; there is no key on the box.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if /usr/local/bin/aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com; then break; fi
  sleep 15
done

docker pull $REPO:$TAG
docker run -d --restart=always --name hairline \
  -p 127.0.0.1:8000:8000 \
  -e OPENCV26_MAX_CONCURRENT_JOBS=1 \
  -e HAIRLINE_REPO_URL=https://github.com/christin09110-stack/hairline \
  $REPO:$TAG

IP=\$(curl -fsS --max-time 10 -H "X-aws-ec2-metadata-token: \$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 120')" http://169.254.169.254/latest/meta-data/public-ipv4)
DASHED=\$(echo \$IP | tr '.' '-')

# sslip.io resolves <dashed-ip>.sslip.io to that address, so Let's Encrypt can
# issue a certificate for it and there is no domain to buy and no load balancer
# to pay for. Port 80 also serves the app directly, so the endpoint still works
# if certificate issuance is rate limited.
mkdir -p /etc/caddy
# Caddyfile braces must open and close on their own lines; a one-line
# `reverse_proxy host { ... }` is a syntax error and caddy will refuse to start.
cat > /etc/caddy/Caddyfile <<CADDY
{
	email opencv26-hairline@proton.me
}

(app) {
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1
	}
	request_body {
		max_size 220MB
	}
}

\$DASHED.sslip.io {
	import app
}

:80 {
	import app
}
CADDY
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

systemctl enable --now caddy
systemctl restart caddy
echo "hairline up at https://\$DASHED.sslip.io" > /var/log/hairline-url
CLOUDINIT

if [[ "$EXISTING" != "None" && -n "$EXISTING" ]]; then
  warn "instance $EXISTING already carries Name=$NAME; not launching a second one"
  state="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$EXISTING" \
    --query 'Reservations[0].Instances[0].State.Name' --output text)"
  [[ "$state" == "stopped" ]] && aws ec2 start-instances --region "$REGION" --instance-ids "$EXISTING" >/dev/null
  INSTANCE_ID="$EXISTING"
else
  say "launching $TYPE"
  INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI" --instance-type "$TYPE" \
    --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$ROLE" \
    --key-name "$KEY_NAME" \
    --user-data "file://$USER_DATA" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$VOLUME_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
    --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
    --tag-specifications \
      "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=opencv26},{Key=Product,Value=hairline},{Key=ManagedBy,Value=hairline-deploy}]" \
      "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=opencv26},{Key=Product,Value=hairline}]" \
    --query 'Instances[0].InstanceId' --output text)"
  ok "launched $INSTANCE_ID"
fi
rm -f "$USER_DATA"

aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
DASHED="${IP//./-}"

ok "$INSTANCE_ID running at $IP"
say "cloud-init installs Docker and Caddy and pulls the image; give it 3 to 5 minutes"
printf '\n  https://%s.sslip.io\n  http://%s\n\n' "$DASHED" "$IP"
warn "this instance bills until: infra/graviton-teardown.sh $INSTANCE_ID"
