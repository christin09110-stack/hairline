# AWS resources and what they cost

Region **us-east-1**, account **<aws-account-id>**. Everything created by this product is
tagged `Project=opencv26` and `Product=hairline`.

**Live endpoint: <https://54-224-119-247.sslip.io>** (also plain
<http://54.224.119.247>). A `c8g.large` — AWS Graviton4 — in us-east-1, instance
`i-0a3a23ae96a63fe64`, running since 2026-09-16 04:11 UTC. **Still running**, which is
the intention: it is the always-on demo endpoint.

`/version` on it reports what a judge needs to check:

```
opencv 5.0.0 | machine aarch64 | threads 2 | baseline NEON FP16
HAL: YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
```

---

## Standing resources

| Resource | Identifier | Rate | Notes |
|---|---|---|---|
| EC2 `c8g.large` | `hairline-demo` | **$0.07976/hr** = $58.23/mo | AWS Graviton4, 2 vCPU, 4 GiB. The always-on demo endpoint. |
| EBS gp3, 30 GiB | attached, delete on terminate | $0.08/GB-mo = **$2.40/mo** | |
| ECR repository | `opencv26/hairline` | $0.10/GB-mo | one arm64 image, about 0.4 GB, so roughly **$0.04/mo** |
| EC2 key pair | `opencv26-hairline` | free | |
| Security group | `opencv26-hairline-sg` | free | 80 and 443 only; **port 22 is not open** |
| IAM role + instance profile | `opencv26-instance-ecr-read` | free | ECR read-only and SSM; a shell is `aws ssm start-session`, so no inbound ssh |
| Elastic IP | **none** | — | the public IPv4 address is charged at $0.005/hr while attached to a running instance, about $3.60/mo, and is included in the EC2 line above |

**Standing total: about $64 a month**, dominated by the instance.

### Why not App Runner, which would be about $2.52 a month

The Cloud Optimized OpenCV Library is delivered as an Amazon Machine Image. It runs
on EC2, on the `c8g`, `m8g` and `r8g` families, and nowhere else. App Runner has no
Arm option in its pricing, its FAQ or its API parameters. A product whose whole
technical argument is about Arm acceleration cannot be demonstrated on an x86-only
service, so this one entry pays for an instance while the other four do not.

### Why no load balancer

An idle Application Load Balancer is $0.0225/hr plus LCU charges: about **$16.20 a
month for zero traffic**. Caddy runs on the instance and gets a certificate from
Let's Encrypt for an `sslip.io` name that resolves to the instance's own address, so
TLS costs nothing and there is no domain to buy.

---

## Benchmark instances

These are launched for the measurement runs and terminated immediately afterwards.
Start and stop times are recorded in [`cool-benchmark.md`](cool-benchmark.md).

| Arm | Instance | EC2 rate | COOL software fee | All-in |
|---|---|---|---|---|
| A, COOL | `c8g.large` | $0.07976/hr | $0.01/hr | **$0.08976/hr** |
| A, COOL (large) | `c8g.4xlarge` | $0.63808/hr | $0.04/hr | **$0.67808/hr** |
| B, stock wheel on Graviton | `c8g.large` | $0.07976/hr | — | $0.07976/hr |
| D, cost-matched x86 | `c7i.4xlarge` | $0.71400/hr | — | $0.71400/hr |

EC2 rates are from the AWS Price List API, queried 2026-09-16, for Linux, shared
tenancy, `preInstalledSw=NA`, `capacitystatus=Used`. The COOL software fees are from
the Marketplace listing's own usage-cost table; they are **not** published through
the Price List API and could not be confirmed programmatically.

### Instance log

Every instance this product started, and when it stopped. An empty "terminated"
column at submission time would be a bill still running.

| Instance id | Type | Purpose | Started (UTC) | Terminated (UTC) | Wall | Cost |
|---|---|---|---|---|---|---|
| `i-06462ffff58676723` | c8g.large | first deploy attempt, wrong image tag | 2026-09-16 04:07 | 2026-09-16 04:09 | 2 min | $0.003 |
| `i-0a3a23ae96a63fe64` | c8g.large | **the live demo endpoint** | 2026-09-16 04:08 | **still running, by design** | — | $0.0798/hr |
| `i-0a6e675cd2c58a483` | c8g.4xlarge (spot) | COOL benchmark, arm B | 2026-09-16 05:00:28 | 2026-09-16 05:09:22 | 9 min | $0.038 |
| `i-036a60724d96032eb` | c7i.4xlarge (spot) | COOL benchmark, arm D | 2026-09-16 05:02:38 | 2026-09-16 05:09:22 | 7 min | $0.036 |

Both benchmark instances are confirmed terminated; `bench/instances.sh list` returns
nothing and `down` terminates by tag rather than by identifier, so a forgotten id
cannot leave one billing. The first deploy attempt was terminated two minutes after
launch: the tag resolver had picked buildx's `buildcache` manifest, which has no
entrypoint, and relaunching was cleaner than patching a running box.

**Total benchmark compute: $0.077.** The standing cost is the demo endpoint.

### What is running right now

One instance, and it is the one that is meant to be:

```
$ aws ec2 describe-instances --region us-east-1 \
    --filters "Name=tag:Project,Values=opencv26"
i-0a3a23ae96a63fe64   c8g.large   running   hairline-demo   2026-09-16T04:08:34+00:00
```

Nothing else. Both benchmark instances are terminated, and the first deploy attempt is
terminated.

---

## Free-tier position

This is an existing AWS account, so the 2025 Free Plan restructure applies: existing
customers are ineligible for the new-customer credits. The perpetual allowances still
apply and are what keeps the other four products near zero.

`t4g.small` is free to 750 hours a month until 31 December 2026, for all customers
including existing ones, aggregated across regions. It was the obvious choice for
this endpoint and was rejected for two reasons. It is burstable, and a sustained 4K
vision workload burns CPU credits and then bills for them at an unpredictable rate;
and it is Graviton2, while COOL targets Graviton4, so the demo endpoint would have
been a different processor generation from the one the benchmark measures. The
$58 a month buys a demo that behaves like the thing being described.

---

## What is blocked, and what it would cost to unblock

**The COOL AMI needs a Marketplace subscription that can only be accepted in the
browser console.** There is no CLI path: the full command inventory of
`marketplace-agreement`, `marketplace-catalog`, `marketplace-deployment`,
`marketplace-entitlement` and `marketplace-reporting` contains no subscribe or
accept-agreement operation. `marketplace-catalog start-change-set` is the seller-side
publishing API, not a buyer action.

Verified not-subscribed three ways:

```
$ aws ec2 run-instances --dry-run --image-id ami-01db31139bc5615d8 \
    --instance-type c8g.large --subnet-id subnet-0b6a703d8a9a3ded3
An error occurred (OptInRequired): In order to use this AWS Marketplace product you
need to accept terms and subscribe. To do so please visit
https://aws.amazon.com/marketplace/pp?sku=aajkmdd4qo3r7yhqg61a7aah9
```

```
$ aws marketplace-agreement search-agreements --catalog AWSMarketplace \
    --filters '[{"name":"PartyType","values":["Acceptor"]},
                {"name":"AgreementType","values":["PurchaseAgreement"]}]'
→ 12 active agreements, all from proposer 979382823631 (Bitnami).
  None from 679593333241 (OpenCV).
```

The same dry run against the plain Ubuntu arm64 AMI returns `DryRunOperation:
Request would have succeeded`, so the block is the Marketplace opt-in and not IAM,
networking or capacity.

The subscription is a **7-day free trial** and then the per-instance-hour software
fee in the table above; there is no separate charge beyond that, so unblocking costs
about $0.01/hr while a benchmark instance runs. Roughly a dollar for the whole
measurement campaign. The manual step is one page:

1. sign in to the console as account <aws-account-id>,
2. open <https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs>,
3. Continue to Subscribe, accept the terms,
4. re-run the dry run above to confirm, then `bench/run.py` with `BENCH_COOL_HOST` set.

**A second blocker, which was requested and is pending.** The account's
*Running On-Demand Standard instances* quota (`L-1216C47A`) is **16 vCPUs, of which
14 are already in use** by six instances belonging to other projects. A `c8g.large`
fits exactly, with nothing to spare. A `c8g.4xlarge` or `c7i.4xlarge` is 16 vCPUs and
cannot launch at all. An increase to 96 was requested on 2026-09-16 at 03:18 UTC, request
`b1833d7d44174757a8c8e5721635679baJHwEe0t`, and **was approved at about 06:40 UTC** —
after the benchmark had already run. It ran on Spot instead, which has a separate and
unused 32-vCPU quota and is the same hardware; the benchmark would not have been
blocked either way, and the increase now removes the constraint for anyone
reproducing it on demand.

Both are recorded here rather than worked around, because the alternative was to
report a benchmark arm that did not run.

## AMI identifiers, for reproduction

Found with `aws ec2 describe-images --owners aws-marketplace --filters
"Name=name,Values=*COOL*"`. The `-prod-XXXX` suffix is the Marketplace offer id and
groups them into four separate listings.

| AMI | Name | Product code | Created |
|---|---|---|---|
| `ami-01db31139bc5615d8` | COOL-Graviton4-v2-AMI-prod-k6u24vxijyzvg | `aajkmdd4qo3r7yhqg61a7aah9` | 2026-04-22 |
| `ami-033e481a24f94c8cb` | Graviton5-COOL-v3-prod-k6u24vxijyzvg | `aajkmdd4qo3r7yhqg61a7aah9` | 2026-07-15 |
| `ami-0b775ed8c83396c73` | COOL-V3-prod-2jrbxn57qotcw | `4eb70u7a6gb5bj1ijix5jlokv` | 2026-01-27 |
| `ami-0c0803594d6330678` | COOL-graviton3-v3-prod-xcqopwa7u5zdu | `9u1i10i9fbqhc92kgsb6ni7k9` | 2026-07-15 |
| `ami-00f40acc34f3d7d39` | COOL Graviton 5-prod-uuhl6kdyx4o3s | `216fpdsdup19yw3vwuiwx254l` | 2026-09-11 |

The Graviton4 listing carries **both** a Graviton4 and a Graviton5 image under one
product code. **UNVERIFIED:** `prodview-fdvbfiewzuehs`, the id on the listing page,
could not be mapped to a product code from the CLI — `describe-entity` returns
`ResourceNotFoundException` because the Catalog API only resolves entities the
account owns as a seller. `ami-01db31139bc5615d8` is inferred from the AMI name
matching "For AWS Graviton4", not from a verified identifier mapping.

The baseline for arm B is the Canonical Ubuntu 24.04 arm64 image,
`ami-0246d714afcc1d494`, resolved from
`/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id`.
