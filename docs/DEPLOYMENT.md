# Deployment Guide

## Sharing Infrastructure Across Apps

If you run multiple apps in the same AWS account, you can point SyncBot at existing resources instead of creating new ones. Every `Existing*` parameter defaults to empty (create new); set it to an existing resource name to reuse it.

| Parameter | What it skips |
|-----------|---------------|
| `ExistingDatabaseHost` | VPC, subnets, security groups, RDS instance |

OAuth and app data use RDS (MySQL); there are no runtime S3 buckets. Example — deploy with an existing RDS:

```bash
sam deploy --guided \
  --parameter-overrides \
    ExistingDatabaseHost=mydb.xxxx.us-east-2.rds.amazonaws.com
```

Each app sharing the same RDS should use a **different `DatabaseSchema`** (the default is `syncbot`). Create the schema and initialize the tables on the existing instance:

```bash
mysql -h <EXISTING_RDS_ENDPOINT> -u <DB_USER> -p -e "CREATE DATABASE IF NOT EXISTS syncbot;"
mysql -h <EXISTING_RDS_ENDPOINT> -u <DB_USER> -p syncbot < db/init.sql
```

**What about API Gateway and Lambda?** Each stack always creates its own API Gateway and Lambda function. These are lightweight resources that don't affect free-tier billing — the free tier quotas (1M API calls, 1M Lambda requests) are shared across your entire account regardless of how many gateways or functions you have. If you want a unified domain across apps, put a CloudFront distribution or API Gateway custom domain in front.

## CI/CD via GitHub Actions

Pushes to `main` automatically build and deploy via `.github/workflows/sam-pipeline.yml`:

1. **Build** — `sam build --use-container`
2. **Deploy to test** — automatic
3. **Deploy to prod** — requires manual approval (configure in GitHub environment settings)

### One-Time Setup

1. **Create an IAM user** for deployments with permissions for CloudFormation, Lambda, API Gateway, S3 (for deploy artifacts only), IAM, and RDS. Generate an access key pair.

2. **Create a SAM deployment bucket** — SAM uploads the Lambda package to an S3 bucket during deploy (packaging only; the app does not use S3 at runtime):

```bash
aws s3 mb s3://my-sam-deploy-bucket --region us-east-2
```

3. **Create GitHub Environments** — Go to your repo → **Settings** → **Environments** and create two environments: `test` and `prod`. For `prod`, enable **Required reviewers** so production deploys need manual approval.

4. **Add GitHub Secrets** — Under **Settings** → **Secrets and variables** → **Actions**, add these as **environment secrets** for both `test` and `prod`:

| Secret | Where to find it |
|--------|-----------------|
| `AWS_ACCESS_KEY_ID` | IAM user access key (step 1) |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key (step 1) |
| `SLACK_SIGNING_SECRET` | Slack app → Basic Information → App Credentials |
| `SLACK_CLIENT_SECRET` | Slack app → Basic Information → App Credentials |
| `DATABASE_PASSWORD` | The RDS master password you chose |
| `PASSWORD_ENCRYPT_KEY` | Any passphrase for bot-token encryption at rest |

5. **Add GitHub Variables** — Under the same settings page, add these as **environment variables** for each environment:

| Variable | `test` value | `prod` value |
|----------|-------------|-------------|
| `AWS_STACK_NAME` | `syncbot-test` | `syncbot-prod` |
| `AWS_S3_BUCKET` | `my-sam-deploy-bucket` | `my-sam-deploy-bucket` |
| `STAGE_NAME` | `staging` | `prod` |

### Deploy Flow

Once configured, merge or push to `main` and the pipeline runs:

```
push to main → sam build → deploy to test → (manual approval) → deploy to prod
```

Monitor progress in your repo's **Actions** tab. The first deploy creates the CloudFormation stack (VPC, RDS, Lambda, API Gateway). SAM uses the deployment bucket only for packaging; the app stores OAuth and data in RDS and uploads media directly to Slack.

> **Tip:** If you prefer to do the very first deploy manually (to see the interactive prompts), run `sam deploy --guided` locally first, then let the pipeline handle all future deploys.
