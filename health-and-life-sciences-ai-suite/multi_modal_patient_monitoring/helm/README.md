# Health AI Suite – Helm Deployment

This Helm chart deploys the **Health & Life Sciences AI Suite** on Kubernetes.


## Prerequisites

- Kubernetes cluster (Minikube / Kind / Bare-metal)
- `kubectl`
- `helm` (v3+)
- A working PersistentVolume provisioner (required for PVC binding)
- **NGINX Ingress Controller** (required when `ingress.enabled: true`, which is the default)

### Ingress Controller prerequisite (required for default configuration)

This chart creates Ingress resources that use `ingressClassName: nginx`. You must have the
NGINX Ingress Controller running in your cluster before installing the chart with ingress enabled.

```bash
# Install NGINX Ingress Controller via Helm
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace

# Wait for the controller to be ready
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s
```

For **Minikube**, enable the built-in ingress addon instead:
```bash
minikube addons enable ingress
```

If you do not have an ingress controller and do not wish to install one, set
`ingress.enabled: false` in `values.yaml` and use port-forwarding to access the
application (see [Access without Ingress](#access-without-ingress-controller) below).

## Required Docker Images

The following images **must exist locally** before deploying the Helm chart.

Check available images:
```bash
docker images | grep intel/hl-ai
```


## Install

```bash
cd health-and-life-sciences-ai-suite/helm/multi_modal_patient_monitoring

helm install multi-modal-patient-monitoring . \
  --namespace multi-modal-patient-monitoring \
  --create-namespace
```

## Upgrade (after changes)
```bash
helm upgrade multi-modal-patient-monitoring . -n multi-modal-patient-monitoring
``` 

## Verify Deployment
Pods
```bash
kubectl get pods -n multi-modal-patient-monitoring
``` 

All pods should be:
```bash
STATUS: Running
READY: 1/1
``` 
## Services
```bash
kubectl get svc -n multi-modal-patient-monitoring
``` 

## Check Logs (recommended)
```bash
kubectl logs -n multi-modal-patient-monitoringi deploy/mdpnp
kubectl logs -n multi-modal-patient-monitoring deploy/dds-bridge
kubectl logs -n multi-modal-patient-monitoring deploy/aggregator
kubectl logs -n multi-modal-patient-monitoring deploy/ai-ecg
kubectl logs -n multi-modal-patient-monitoring deploy/pose
kubectl logs -n multi-modal-patient-monitoring deploy/metrics
kubectl logs -n multi-modal-patient-monitoring deploy/ui
``` 

Healthy services will show:

- Application startup complete
- Listening on expected ports
- No crash loops


## Ingress Configuration

The chart creates two Ingress resources when `ingress.enabled: true` (default):

| Value | Default | Description |
|---|---|---|
| `ingress.enabled` | `true` | Create Ingress resources for external access |
| `ingress.className` | `nginx` | IngressClass to use (requires a matching controller) |
| `ingress.annotations` | *(nginx-specific)* | Annotations applied to the main ingress |
| `ingress.hosts` | `multi-modal-patient-monitoring.local` | Hostname and path routing rules |

To disable ingress (e.g., for environments without an ingress controller):

```bash
helm install multi-modal-patient-monitoring . \
  --namespace multi-modal-patient-monitoring \
  --create-namespace \
  --set ingress.enabled=false
```

## Access the Frontend UI

### With Ingress (default)
#### 1. Check the Ingress Resource

Run the following command to view the ingress configuration:

```bash
kubectl get ingress -n multi-modal-patient-monitoring
```
This will show the hostname or IP and the path for the UI.

Example output:
```bash
NAME       HOSTS               PATHS   ADDRESS         PORTS
multi-modal-patient-monitoring  multi-modal-patient-monitoring.local       /       xx.xx.xx.xx   80
```
#### 2. If an IP Address Appears in ADDRESS

Add an entry on your Linux host (replace <IP> with the one you found):
```bash
echo "<IP> multi-modal-patient-monitoring.local" | sudo tee -a /etc/hosts
```

#### 3. If the ADDRESS Field is Empty (Common in Minikube)
Some local Kubernetes environments (such as Minikube) do not automatically populate the ingress IP.

Retrieve the Minikube cluster IP:
```bash
minikube ip
```
Then map the hostname to the IP:
```bash
echo "$(minikube ip) multi-modal-patient-monitoring.local" | sudo tee -a /etc/hosts
```

#### 4. Enable Ingress in Minikube (if not already enabled)
```bash
minikube addons enable ingress
```
Wait a few moments for the ingress controller to start.

#### 5.Open the Application
Open your browser and navigate to:
```bash
http://<host-or-ip>/
``` 
Example:
```bash
http://multi-modal-patient-monitoring.local/
``` 
If using Minikube, you may need to enable the ingress addon:
```bash
minikube addons enable ingress
``` 
This will open the Health AI Suite frontend dashboard.

From here you can access:

  - 3D Pose Estimation

  - ECG Monitoring

  - RPPG Monitoring

  - MdPnP service

  - Metrics Dashboard

### Access without Ingress Controller

If you deployed with `ingress.enabled: false` or do not have an NGINX Ingress Controller,
you can access the application using `kubectl port-forward`.

#### Forward the UI service
```bash
kubectl port-forward -n multi-modal-patient-monitoring svc/ui 8080:80
```
Then open http://localhost:8080 in your browser.

#### Forward the Aggregator API (video streams and API)
```bash
kubectl port-forward -n multi-modal-patient-monitoring svc/aggregator 8081:80
```
The aggregator API is then available at http://localhost:8081.

#### Forward the Pose Estimation stream
```bash
kubectl port-forward -n multi-modal-patient-monitoring svc/pose 8085:8085
```
The pose video feed is then available at http://localhost:8085/video_feed and
http://localhost:8085/pose-video.

> **Tip:** You can run all three `port-forward` commands in separate terminal windows
> to access the full application simultaneously.

#### Alternative: NodePort access

You can also switch the services to NodePort type by overriding the service type:

```bash
helm install multi-modal-patient-monitoring . \
  --namespace multi-modal-patient-monitoring \
  --create-namespace \
  --set ingress.enabled=false \
  --set service.type=NodePort
```

Then find the assigned node ports:
```bash
kubectl get svc -n multi-modal-patient-monitoring
```

Access the services at `http://<node-ip>:<node-port>`.


## Uninstall
```bash
helm uninstall multi-modal-patient-monitoring -n multi-modal-patient-monitoring
``` 