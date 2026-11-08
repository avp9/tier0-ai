// One Tier0 container app (used for staging and production).

param name string
param location string
param environmentId string
param image string
@allowed(['staging', 'production'])
param appEnv string
@secure()
param databaseUrl string

@secure()
param sessionSecret string
@secure()
param openaiApiKey string
@secure()
param typesafeApiKey string
@secure()
param jiraApiToken string
@secure()
param demoUsers string

param openaiModelName string
param jiraServer string
param jiraUsername string
param jiraProjectKey string
@description('Scale-to-zero saves money; set to 1 to avoid cold starts during a demo.')
@minValue(0)
@maxValue(1)
param minReplicas int = 0

var secrets = [
  { name: 'database-url', value: databaseUrl }
  { name: 'session-secret', value: sessionSecret }
  { name: 'openai-api-key', value: openaiApiKey }
  { name: 'typesafe-api-key', value: typesafeApiKey }
  { name: 'jira-api-token', value: jiraApiToken }
  { name: 'demo-users', value: demoUsers }
]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: secrets
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'tier0'
          image: image
          // 2 GiB: the SPLADE and reranker models take about 1 GB once loaded.
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'APP_ENV', value: appEnv }
            // One reverse proxy (the Container Apps ingress) sits in front of the app.
            { name: 'TRUSTED_PROXY_HOPS', value: '1' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'SESSION_SECRET', secretRef: 'session-secret' }
            { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
            { name: 'TYPESAFE_API_KEY', secretRef: 'typesafe-api-key' }
            { name: 'JIRA_API_TOKEN', secretRef: 'jira-api-token' }
            { name: 'DEMO_USERS', secretRef: 'demo-users' }
            { name: 'OPENAI_MODEL_NAME', value: openaiModelName }
            { name: 'JIRA_SERVER', value: jiraServer }
            { name: 'JIRA_USERNAME', value: jiraUsername }
            { name: 'JIRA_PROJECT_KEY', value: jiraProjectKey }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/api/health', port: 8000 }
              periodSeconds: 5
              failureThreshold: 24
            }
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8000 }
              periodSeconds: 30
            }
          ]
        }
      ]
      // One replica at most: the chat and login rate limits are counted in memory per replica.
      scale: { minReplicas: minReplicas, maxReplicas: 1 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
