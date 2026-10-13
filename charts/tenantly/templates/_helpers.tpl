{{/* Chart name, truncated to the 63-character DNS label limit. */}}
{{- define "tenantly.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Release-scoped resource name: <release>-<chart>, unless the release name already has the chart name. */}}
{{- define "tenantly.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Labels applied to every resource. */}}
{{- define "tenantly.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "tenantly.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: kubestack
{{- end }}

{{/* Labels used in selectors. Each workload adds app.kubernetes.io/component on top. */}}
{{- define "tenantly.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tenantly.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Name of the Secret the API reads: the existing one if set, otherwise <fullname>-secrets. */}}
{{- define "tenantly.secretName" -}}
{{- if .Values.secrets.existingSecretName }}
{{- .Values.secrets.existingSecretName }}
{{- else }}
{{- printf "%s-secrets" (include "tenantly.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
