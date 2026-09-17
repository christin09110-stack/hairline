#!/usr/bin/env bash
# Launch and terminate the benchmark instances for the COOL comparison.
#
#   bench/instances.sh up graviton   # c8g.4xlarge, Graviton4, stock pip wheel
#   bench/instances.sh up hybrid     # c7i.4xlarge, x86, cost-matched
#   bench/instances.sh up cool       # c8g.4xlarge from the COOL AMI (needs the subscription)
#   bench/instances.sh list
#   bench/instances.sh down          # terminate everything this script started
#
# **Spot, not on-demand, and that is not a cost decision.** This account's
# `Running On-Demand Standard instances` quota is 16 vCPUs and 14 are already used
# by other projects, so a 16-vCPU on-demand benchmark instance cannot launch at all
# (`VcpuLimitExceeded`). The Spot quota is a separate 32 vCPUs and is unused. An
# increase to 96 was requested and is open as a support case; until it lands, Spot
# is the only way to run a 4xlarge, and for a benchmark that runs for ten minutes
# and is then thrown away it is the right shape anyway.
#
# Every instance is tagged Project=opencv26 Product=hairline Role=benchmark, and
# `down` terminates by that tag so nothing can be left behind by forgetting an id.

set -euo pipefail
export AWS_PAGER=""

REGION="${AWS_REGION:-us-east-1}"
KEY="${BENCH_KEY_NAME:-opencv26-hairline}"
SG_NAME="opencv26-hairline-bench-sg"
UBUNTU_SSM="/aws/service/canonical/ubuntu/server/24.04/stable/current/%s/hvm/ebs-gp3/ami-id"
COOL_AMI="${COOL_AMI:-ami-01db31139bc5615d8}"

say(){ printf '\033[34m==>\033[0m %s\n' "$*"; }
ok(){ printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn(){ printf '\033[33mwarn\033[0m %s\n' "$*"; }

security_group() {
  local vpc sg me
  vpc="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
        --query 'Vpcs[0].VpcId' --output text)"
  sg="$(aws ec2 describe-security-groups --region "$REGION" \
        --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$vpc" \
        --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)"
  if [[ "$sg" == "None" || -z "$sg" ]]; then
    sg="$(aws ec2 create-security-group --region "$REGION" --group-name "$SG_NAME" \
          --vpc-id "$vpc" --description "opencv26 hairline benchmark: ssh from one address" \
          --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=opencv26},{Key=Product,Value=hairline}]" \
          --query GroupId --output text)"
  fi
  # checkip.amazonaws.com does not always resolve from here, so try a few.
  me=""
  for probe in https://ifconfig.me https://checkip.amazonaws.com https://api.ipify.org; do
    me="$(curl -fsS --max-time 10 "$probe" 2>/dev/null | tr -d '[:space:]')" || true
    [[ "$me" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && break
    me=""
  done
  [[ -n "${BENCH_SSH_CIDR:-}" ]] && me="${BENCH_SSH_CIDR%%/*}"
  [[ -n "$me" ]] || { echo "could not determine this machine's address" >&2; exit 1; }
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$sg" \
    --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$me/32,Description=bench}]" \
    >/dev/null 2>&1 || true
  echo "$sg"
}

user_data() {  # $1 = arch tag for the wheel; COOL images already have their own venv
  cat <<'CLOUDINIT' | base64 -w0
#!/bin/bash
exec > >(tee /var/log/bench-boot.log) 2>&1
set -x
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip
python3 -m venv /opt/bench
# Pinned to the same wheel the product pins. An unpinned install resolves to 4.14.x.
/opt/bench/bin/pip install --no-cache-dir "opencv-python-headless==5.0.0.93" "numpy==2.5.3"
/opt/bench/bin/python -c "import cv2; print(cv2.__version__)" > /var/log/bench-ready
CLOUDINIT
}

launch() {  # $1 name  $2 type  $3 ami  $4 arch
  local name="$1" type="$2" ami="$3" sg
  # The COOL AMI's root snapshot is 50 GB and it ships its own venvs, so it gets a
  # 50 GB root and no cloud-init: installing our wheel there would put a second
  # OpenCV next to the one being measured.
  local disk=24 udata
  udata="$(user_data)"
  if [[ "$name" == "cool" ]]; then disk=50; udata="$(printf '#!/bin/bash\ntouch /var/log/bench-ready\n' | base64 -w0)"; fi
  sg="$(security_group)"
  say "requesting spot $type for $name"
  local id
  id="$(aws ec2 run-instances --region "$REGION" \
    --image-id "$ami" --instance-type "$type" \
    --security-group-ids "$sg" --key-name "$KEY" \
    --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
    --user-data "$udata" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":'"$disk"',"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
    --tag-specifications \
      "ResourceType=instance,Tags=[{Key=Name,Value=hairline-bench-$name},{Key=Project,Value=opencv26},{Key=Product,Value=hairline},{Key=Role,Value=benchmark}]" \
      "ResourceType=volume,Tags=[{Key=Project,Value=opencv26},{Key=Product,Value=hairline},{Key=Role,Value=benchmark}]" \
    --query 'Instances[0].InstanceId' --output text)"
  ok "launched $id"
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$id"
  local dns launched
  dns="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$id" \
        --query 'Reservations[0].Instances[0].PublicDnsName' --output text)"
  launched="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$id" \
        --query 'Reservations[0].Instances[0].LaunchTime' --output text)"
  ok "$id  $dns  launched $launched"
  local py=/opt/bench/bin/python
  [[ "$name" == "cool" ]] && py=/opt/cool/venvs/python_3.12/bin/python
  printf 'export BENCH_%s_HOST=ubuntu@%s\nexport BENCH_%s_TYPE=%s\nexport BENCH_%s_PYTHON=%s\n' \
    "${name^^}" "$dns" "${name^^}" "$type" "${name^^}" "$py"
  if [[ "$name" == "cool" ]]; then
    warn "COOL's cv2 needs the PYTHONPATH/LD_LIBRARY_PATH its activate script sets; bench/arms.py sets both"
  else
    warn "cloud-init installs the wheel; wait for /var/log/bench-ready before benchmarking"
  fi
}

case "${1:-}" in
  up)
    case "${2:-}" in
      graviton) launch graviton "${3:-c8g.4xlarge}" \
        "$(aws ssm get-parameter --region "$REGION" --name "$(printf "$UBUNTU_SSM" arm64)" --query Parameter.Value --output text)" ;;
      hybrid)   launch hybrid "${3:-c7i.4xlarge}" \
        "$(aws ssm get-parameter --region "$REGION" --name "$(printf "$UBUNTU_SSM" amd64)" --query Parameter.Value --output text)" ;;
      cool)     launch cool "${3:-c8g.4xlarge}" "$COOL_AMI" ;;
      *) echo "usage: $0 up {graviton|hybrid|cool} [instance-type]" >&2; exit 2 ;;
    esac ;;
  list)
    aws ec2 describe-instances --region "$REGION" \
      --filters "Name=tag:Role,Values=benchmark" "Name=tag:Product,Values=hairline" \
                "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,PublicDnsName,LaunchTime]' \
      --output text ;;
  down)
    ids="$(aws ec2 describe-instances --region "$REGION" \
      --filters "Name=tag:Role,Values=benchmark" "Name=tag:Product,Values=hairline" \
                "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].InstanceId' --output text)"
    if [[ -z "$ids" ]]; then ok "nothing to terminate"; exit 0; fi
    say "terminating $ids"
    aws ec2 terminate-instances --region "$REGION" --instance-ids $ids \
      --query 'TerminatingInstances[].[InstanceId,CurrentState.Name]' --output text
    aws ec2 wait instance-terminated --region "$REGION" --instance-ids $ids
    ok "all benchmark instances terminated at $(date -u +%Y-%m-%dT%H:%M:%SZ)" ;;
  *) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
