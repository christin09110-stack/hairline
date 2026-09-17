# infra

Shell scripts that reconcile AWS to a desired state, print what they did, and are safe
to re-run. Region `us-east-1`, account `<aws-account-id>`, everything tagged
`Project=opencv26` and `Product=hairline`. Set `DRY_RUN=1` to see the commands without
running them.

| Script | What it does |
|---|---|
| `ecr.sh <product>` | Create or reuse a private ECR repo, log in, buildx, push |
| `graviton-teardown.sh <id>` | Terminate an instance, and its security group if nothing else uses it |
| `common.sh` | Shared helpers and the account check |

Hairline's own deploy is `products/hairline/infra/deploy.sh` (a Graviton4 `c8g.large`
behind Caddy), and the COOL benchmark instances come from
`products/hairline/bench/instances.sh`.

```bash
infra/ecr.sh hairline --context . --dockerfile products/hairline/Dockerfile --arch linux/arm64
products/hairline/infra/deploy.sh
```

`ecr.sh` defaults to `--arch linux/amd64`; Graviton needs `--arch linux/arm64`.
`graviton-teardown.sh` will not run without an instance id, and will not touch an
instance that is not tagged `Project=opencv26`.
