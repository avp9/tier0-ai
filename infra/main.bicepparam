using 'main.bicep'

// Secrets are read from environment variables of whoever runs the deployment (the owner, or the
// deploy pipeline from GitHub Environment secrets). Nothing secret is stored in this file.
param postgresAdminPassword = readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')
param stagingSessionSecret = readEnvironmentVariable('STAGING_SESSION_SECRET')
param productionSessionSecret = readEnvironmentVariable('PRODUCTION_SESSION_SECRET')
param openaiApiKey = readEnvironmentVariable('OPENAI_API_KEY')
param typesafeApiKey = readEnvironmentVariable('TYPESAFE_API_KEY')
param jiraApiToken = readEnvironmentVariable('JIRA_API_TOKEN')
param stagingDemoUsers = readEnvironmentVariable('STAGING_DEMO_USERS')
param productionDemoUsers = readEnvironmentVariable('PRODUCTION_DEMO_USERS')

param jiraServer = readEnvironmentVariable('JIRA_SERVER')
param jiraUsername = readEnvironmentVariable('JIRA_USERNAME')

// The images currently running, so an infrastructure change does not reset them to the
// placeholder. Leave unset for a first deployment.
param stagingImage = readEnvironmentVariable('STAGING_IMAGE', 'mcr.microsoft.com/k8se/quickstart:latest')
param productionImage = readEnvironmentVariable('PRODUCTION_IMAGE', 'mcr.microsoft.com/k8se/quickstart:latest')
