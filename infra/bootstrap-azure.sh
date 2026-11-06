#!/usr/bin/env bash
# One-time Azure and GitHub setup for Tier0, run by the owner. Safe to re-run: every step checks
# whether it is already done. It asks before each step that changes anything, and stores no secrets.
#
# Creates: resource group, an Entra app registration with federated (OIDC) credentials for the
# GitHub environments, a Contributor role scoped to ONLY that resource group, and the GitHub
# environments (production requires your approval). Needs: az, gh, both signed in.
set -euo pipefail

REPO="${REPO:-avp9/tier0-ai}"
RG="${RG:-rg-tier0}"
LOCATION="${LOCATION:-eastus}"
APP_NAME="${APP_NAME:-tier0-github-deploy}"

confirm() { read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy]$ ]] || { echo "Skipped."; return 1; }; }

command -v az >/dev/null || { echo "Install the Azure CLI first."; exit 1; }
command -v gh >/dev/null || { echo "Install the GitHub CLI first."; exit 1; }
az account show >/dev/null 2>&1 || { echo "Run: az login"; exit 1; }

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
echo "Subscription: $(az account show --query name -o tsv) ($SUB_ID)"
echo "Repository:   $REPO    Resource group: $RG ($LOCATION)"
confirm "Use this subscription?" || exit 1

echo "== 1. Resource providers =="
if confirm "Register Microsoft.App, OperationalInsights and DBforPostgreSQL?"; then
  for p in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL; do
    az provider register --namespace "$p" --wait
  done
fi

echo "== 2. Resource group =="
if [[ "$(az group exists --name "$RG")" == "false" ]]; then
  confirm "Create resource group $RG in $LOCATION?" && az group create --name "$RG" --location "$LOCATION" -o none
else
  echo "$RG already exists."
fi

echo "== 3. App registration for GitHub (no secret is created) =="
CLIENT_ID=$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)
if [[ -z "$CLIENT_ID" ]]; then
  confirm "Create app registration $APP_NAME?" || exit 1
  CLIENT_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  az ad sp create --id "$CLIENT_ID" -o none
fi
echo "Client ID: $CLIENT_ID"

echo "== 4. Federated credentials: only these GitHub environments can sign in =="
for ENV_NAME in staging production; do
  CRED="github-$ENV_NAME"
  EXISTING=$(az ad app federated-credential list --id "$CLIENT_ID" --query "[?name=='$CRED'].name" -o tsv)
  if [[ -z "$EXISTING" ]]; then
    confirm "Trust $REPO environment '$ENV_NAME'?" && az ad app federated-credential create --id "$CLIENT_ID" --parameters "{
      \"name\": \"$CRED\", \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"repo:$REPO:environment:$ENV_NAME\", \"audiences\": [\"api://AzureADTokenExchange\"]}" -o none
  else
    echo "$CRED already exists."
  fi
done

echo "== 5. Role: Contributor on $RG only (not the subscription) =="
SCOPE="/subscriptions/$SUB_ID/resourceGroups/$RG"
if [[ -z "$(az role assignment list --assignee "$CLIENT_ID" --scope "$SCOPE" --query '[0].id' -o tsv)" ]]; then
  confirm "Grant Contributor on $RG to $APP_NAME?" && az role assignment create \
    --assignee "$CLIENT_ID" --role Contributor --scope "$SCOPE" -o none
else
  echo "Role already assigned."
fi

echo "== 6. GitHub environments and variables =="
if confirm "Create GitHub environments (production requires your approval) and set Azure IDs?"; then
  OWNER_ID=$(gh api user --jq .id)
  gh api -X PUT "repos/$REPO/environments/staging" -o /dev/null
  gh api -X PUT "repos/$REPO/environments/production" --input - >/dev/null <<JSON
{"reviewers":[{"type":"User","id":$OWNER_ID}],"deployment_branch_policy":{"protected_branches":true,"custom_branch_policies":false}}
JSON
  gh variable set AZURE_CLIENT_ID --repo "$REPO" --body "$CLIENT_ID"
  gh variable set AZURE_TENANT_ID --repo "$REPO" --body "$TENANT_ID"
  gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUB_ID"
  gh variable set AZURE_RESOURCE_GROUP --repo "$REPO" --body "$RG"
fi

cat <<NEXT

Done. Still to do by hand (see docs/azure-setup.md):
  - export the secrets listed there and run the first infrastructure deployment;
  - make the GHCR package public once the first image is pushed.
NEXT
