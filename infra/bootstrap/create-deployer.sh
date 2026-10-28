#!/usr/bin/env bash
# NightWatch one-time AWS setup. Run it in AWS CloudShell while signed in as the account owner:
#
#   git clone https://github.com/RaagulGananathan13/nightwatch.git
#   bash nightwatch/infra/bootstrap/create-deployer.sh
#
# It only creates new things and changes nothing that already exists:
#   1. IAM policy /nightwatch/NightWatchBoundary: the most NightWatch may ever do (the JSON file
#      next to this script). It is the deployer's permission AND the permissions boundary that
#      every role Terraform creates must carry, so nothing NightWatch builds can exceed it.
#   2. IAM user nightwatch-deployer: Terraform and the chaos scripts run as this user.
#   3. One access key for that user, printed as `aws configure` commands for your laptop.
#
# Re-running is safe: it publishes the current policy as a new version and reuses the user.
# A new access key is made only when the user has none, or when you pass --new-key.
set -euo pipefail

REGION="${NW_REGION:-us-east-2}"
POLICY_NAME="NightWatchBoundary"
USER_NAME="nightwatch-deployer"
PROFILE="nightwatch"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/nightwatch-boundary-policy.json"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/nightwatch/${POLICY_NAME}"
echo "Account ${ACCOUNT_ID}, region ${REGION}"

# 1. Policy: fill in the account and region, then create it or publish it as a new version.
DOC="$(mktemp)"
trap 'rm -f "$DOC"' EXIT
sed -e "s/__ACCOUNT_ID__/${ACCOUNT_ID}/g" -e "s/__REGION__/${REGION}/g" "$TEMPLATE" > "$DOC"
python3 -m json.tool "$DOC" > /dev/null # stop here if the document is broken

if aws iam get-policy --policy-arn "$POLICY_ARN" > /dev/null 2>&1; then
  # IAM keeps at most five versions, so drop the oldest non-default one when it is full.
  old_versions=$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
    --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)
  if [ "$(wc -w <<< "$old_versions")" -ge 4 ]; then
    oldest=$(tr '\t ' '\n\n' <<< "$old_versions" | sort -V | head -n 1)
    aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$oldest"
  fi
  aws iam create-policy-version --policy-arn "$POLICY_ARN" \
    --policy-document "file://$DOC" --set-as-default > /dev/null
  echo "Updated policy ${POLICY_ARN}"
else
  aws iam create-policy --policy-name "$POLICY_NAME" --path /nightwatch/ \
    --policy-document "file://$DOC" \
    --description "NightWatch: everything NightWatch may do (deployer policy and role boundary)" \
    --tags Key=project,Value=nightwatch > /dev/null
  echo "Created policy ${POLICY_ARN}"
fi

# 2. The deployer user.
if aws iam get-user --user-name "$USER_NAME" > /dev/null 2>&1; then
  echo "User ${USER_NAME} already exists"
else
  aws iam create-user --user-name "$USER_NAME" --path /nightwatch/ \
    --tags Key=project,Value=nightwatch > /dev/null
  echo "Created user ${USER_NAME}"
fi
aws iam attach-user-policy --user-name "$USER_NAME" --policy-arn "$POLICY_ARN"

# 3. An access key for the laptop.
key_count=$(aws iam list-access-keys --user-name "$USER_NAME" \
  --query 'length(AccessKeyMetadata)' --output text)
if [ "$key_count" = "0" ] || [ "${1:-}" = "--new-key" ]; then
  if [ "$key_count" -ge 2 ]; then
    echo "The user already has two access keys (the IAM limit). Delete one in the IAM console, then re-run." >&2
    exit 1
  fi
  read -r KEY_ID SECRET < <(aws iam create-access-key --user-name "$USER_NAME" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
  cat << EOF

Run these three lines on your laptop (PowerShell or Git Bash), then clear this screen:

aws configure set aws_access_key_id ${KEY_ID} --profile ${PROFILE}
aws configure set aws_secret_access_key ${SECRET} --profile ${PROFILE}
aws configure set region ${REGION} --profile ${PROFILE}

EOF
else
  echo "The user already has an access key, so no new one was made. Pass --new-key to make another."
fi

# 4. Something Terraform needs to know.
if aws iam list-open-id-connect-providers --output text | grep -q 'token.actions.githubusercontent.com'; then
  echo "Note: this account already has a GitHub OIDC provider."
  echo "      Set create_github_oidc_provider = false in infra/envs/dev/terraform.tfvars."
fi
echo "Done."
