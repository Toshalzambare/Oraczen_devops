{{/*
Expand the name of the chart.
*/}}
{{- define "notes-api.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "notes-api.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "notes-api.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "notes-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels used by the Deployment and Service.
*/}}
{{- define "notes-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "notes-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
The name of the Secret created by the postgresql subchart.
Bitnami's postgresql chart names the secret: <release-name>-postgresql
*/}}
{{- define "notes-api.postgresSecretName" -}}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}
