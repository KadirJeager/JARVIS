"""Render the Cloud Run service definition for `gcloud run services replace`.

Reads installation values from the environment (set by `deploy.sh`) and
prints a Knative Service as JSON, which the command accepts as YAML. The
CLIProxyAPI sidecar is included only when the installation uses it.
"""

import json
import os


def env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise SystemExit(f'{name} is required')
    return value


def main() -> None:
    core_env = {
        'JARVIS_PROJECT_ID': env('PROJECT_ID'),
        'JARVIS_ALLOWED_EMAILS': env('ALLOWED_EMAILS'),
        'JARVIS_SERVICE_URL': env('SERVICE_URL'),
        'JARVIS_TASKS_QUEUE': env('TASKS_QUEUE'),
        'JARVIS_INVOKER_SERVICE_ACCOUNT': env('INVOKER_SA'),
        'JARVIS_FIREBASE_WEB_CONFIG': env('FIREBASE_WEB_CONFIG'),
        'JARVIS_VAPID_KEY_REF': env('VAPID_KEY_REF'),
        'JARVIS_SECRET_PREFIX': env('SECRET_PREFIX'),
        # The image tag, shown on the panel's system page.
        'JARVIS_BUILD': env('CORE_IMAGE').rsplit(':', 1)[-1],
    }
    containers = [
        {
            'name': 'core',
            'image': env('CORE_IMAGE'),
            'ports': [{'name': 'http1', 'containerPort': 8080}],
            'env': [{'name': name, 'value': value} for name, value in core_env.items()],
            'resources': {'limits': {'cpu': '1', 'memory': '1Gi'}},
            'startupProbe': {'httpGet': {'path': '/health', 'port': 8080}, 'periodSeconds': 2, 'failureThreshold': 30},
        }
    ]
    volumes = []
    if os.environ.get('CLIPROXY') == '1':
        containers.append(
            {
                'name': 'cliproxy',
                'image': env('CLIPROXY_IMAGE'),
                'env': [
                    {
                        'name': 'CLIPROXY_API_KEY',
                        'valueFrom': {'secretKeyRef': {'name': env('CLIPROXY_KEY_SECRET'), 'key': 'latest'}},
                    }
                ],
                'volumeMounts': [{'name': 'oauth-auth', 'mountPath': '/secrets/auth', 'readOnly': True}],
                'resources': {'limits': {'cpu': '500m', 'memory': '256Mi'}},
                'startupProbe': {'tcpSocket': {'port': 8317}, 'periodSeconds': 5, 'failureThreshold': 12, 'timeoutSeconds': 3},
            }
        )
        volumes.append(
            {
                'name': 'oauth-auth',
                'secret': {
                    'secretName': env('CLIPROXY_OAUTH_SECRET'),
                    'items': [{'key': 'latest', 'path': 'antigravity.json'}],
                },
            }
        )
    service = {
        'apiVersion': 'serving.knative.dev/v1',
        'kind': 'Service',
        'metadata': {'name': env('SERVICE'), 'annotations': {'run.googleapis.com/ingress': 'all'}},
        'spec': {
            'template': {
                'metadata': {
                    'annotations': {
                        'autoscaling.knative.dev/maxScale': '3',
                        'run.googleapis.com/execution-environment': 'gen2',
                        'run.googleapis.com/startup-cpu-boost': 'true',
                    }
                },
                'spec': {
                    'serviceAccountName': env('RUNTIME_SA'),
                    # A turn runs inside the Cloud Tasks request; tasks allow up to 30 minutes.
                    'timeoutSeconds': 1800,
                    'containerConcurrency': 20,
                    'containers': containers,
                    'volumes': volumes,
                },
            },
            'traffic': [{'percent': 100, 'latestRevision': True}],
        },
    }
    print(json.dumps(service, indent=2))


if __name__ == '__main__':
    main()
