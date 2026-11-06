// Tier0 AI on Azure: one resource group holding everything (deploy with `az deployment group`).
//   Log Analytics, a Container Apps environment (Consumption), two apps (staging, production),
//   and one PostgreSQL Flexible Server (B1ms) with a database per environment.
// Secrets arrive as secure parameters from the deploy pipeline; nothing secret is in this repo.

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Image for both apps until the deploy pipeline replaces it with a tagged build.')
param bootstrapImage string = 'mcr.microsoft.com/k8se/quickstart:latest'
@description('Images currently deployed; pass the live values on infra changes so they are kept.')
param stagingImage string = bootstrapImage
param productionImage string = bootstrapImage

param postgresAdminLogin string = 'tier0admin'
@secure()
param postgresAdminPassword string

@secure()
param stagingSessionSecret string
@secure()
param productionSessionSecret string
@secure()
param openaiApiKey string
@secure()
param typesafeApiKey string
@secure()
param jiraApiToken string
@secure()
param stagingDemoUsers string
@secure()
param productionDemoUsers string

param openaiModelName string = 'gpt-4o-mini'
param jiraServer string
param jiraUsername string
param jiraProjectKey string = 'SUP'
param productionMinReplicas int = 0

var postgresName = 'psql-tier0-${uniqueString(resourceGroup().id)}'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-tier0'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // a runaway log cannot become a surprise bill
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-tier0'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    authConfig: { activeDirectoryAuth: 'Disabled', passwordAuth: 'Enabled' }
  }
}

// Public endpoint, open only to Azure services (the 0.0.0.0 rule); every connection needs TLS and a
// password. A private network would cost more (see docs/azure-setup.md, "Known limits").
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource stagingDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'tier0_staging'
}

resource productionDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'tier0_prod'
}

var dbHost = postgres.properties.fullyQualifiedDomainName
var encodedPassword = uriComponent(postgresAdminPassword)

module staging 'modules/app.bicep' = {
  name: 'app-staging'
  params: {
    name: 'ca-tier0-staging'
    location: location
    environmentId: environment.id
    image: stagingImage
    appEnv: 'staging'
    databaseUrl: 'postgresql://${postgresAdminLogin}:${encodedPassword}@${dbHost}:5432/${stagingDb.name}?sslmode=require'
    sessionSecret: stagingSessionSecret
    openaiApiKey: openaiApiKey
    typesafeApiKey: typesafeApiKey
    jiraApiToken: jiraApiToken
    demoUsers: stagingDemoUsers
    openaiModelName: openaiModelName
    jiraServer: jiraServer
    jiraUsername: jiraUsername
    jiraProjectKey: jiraProjectKey
  }
}

module production 'modules/app.bicep' = {
  name: 'app-production'
  params: {
    name: 'ca-tier0-prod'
    location: location
    environmentId: environment.id
    image: productionImage
    appEnv: 'production'
    databaseUrl: 'postgresql://${postgresAdminLogin}:${encodedPassword}@${dbHost}:5432/${productionDb.name}?sslmode=require'
    sessionSecret: productionSessionSecret
    openaiApiKey: openaiApiKey
    typesafeApiKey: typesafeApiKey
    jiraApiToken: jiraApiToken
    demoUsers: productionDemoUsers
    openaiModelName: openaiModelName
    jiraServer: jiraServer
    jiraUsername: jiraUsername
    jiraProjectKey: jiraProjectKey
    minReplicas: productionMinReplicas
  }
}

output stagingUrl string = 'https://${staging.outputs.fqdn}'
output productionUrl string = 'https://${production.outputs.fqdn}'
output postgresHost string = dbHost
