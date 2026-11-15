# Raw system evidence

## DVC
```
data-v1         Initialize DVC and track dataset v1 (400 rows)
data-v2         Dataset v2: full collection (569 rows)

train:
	changed deps:
		modified:           training\train.py

outs:
- md5: bfa7a20347006b0d0bad2f6fc32f66ca
  size: 121955
  hash: md5
  path: dataset.csv
--- data-v1:
outs:
- md5: 1bd0667cf3f8ec8f1dbb204a4a057e2d
  size: 85940
  hash: md5
  path: dataset.csv
```

## Kubernetes
```
NAME                             READY   UP-TO-DATE   AVAILABLE   AGE    CONTAINERS       IMAGES                 SELECTOR
deployment.apps/prediction-api   2/2     2            2           3h2m   prediction-api   prediction-api:1.0.0   app=prediction-api

NAME                                  READY   STATUS    RESTARTS   AGE   IP            NODE                  NOMINATED NODE   READINESS GATES
pod/prediction-api-5f9bc475b4-jqqhd   1/1     Running   0          32s   10.244.0.11   mlops-control-plane   <none>           <none>
pod/prediction-api-5f9bc475b4-wfspf   1/1     Running   0          44s   10.244.0.10   mlops-control-plane   <none>           <none>

NAME                     TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)   AGE    SELECTOR
service/prediction-api   ClusterIP   10.96.95.192   <none>        80/TCP    3h2m   app=prediction-api

NAME                              DATA   AGE
configmap/kube-root-ca.crt        1      3h2m
configmap/prediction-api-config   5      3h2m

Name:                   prediction-api
Namespace:              ml-serving
CreationTimestamp:      Sun, 15 Nov 2026 19:36:01 +0500
Labels:                 app=prediction-api
                        app.kubernetes.io/name=prediction-api
                        app.kubernetes.io/version=1.0.0
Annotations:            deployment.kubernetes.io/revision: 4
Selector:               app=prediction-api
Replicas:               2 desired | 2 updated | 2 total | 2 available | 0 unavailable
StrategyType:           RollingUpdate
MinReadySeconds:        0
RollingUpdateStrategy:  0 max unavailable, 1 max surge
Pod Template:
  Labels:       app=prediction-api
                app.kubernetes.io/name=prediction-api
                app.kubernetes.io/version=1.0.0
  Annotations:  kubectl.kubernetes.io/restartedAt: 2026-11-15T22:37:22+05:00
                mlops/model-version: champion
  Containers:
   prediction-api:
    Image:      prediction-api:1.0.0
    Port:       8000/TCP (http)
    Host Port:  0/TCP (http)
    Limits:
      cpu:     500m
      memory:  512Mi
    Requests:
      cpu:      100m
      memory:   256Mi
    Liveness:   http-get http://:http/health/live delay=15s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:http/health/ready delay=5s timeout=1s period=5s #success=1 #failure=3
    Environment Variables from:
      prediction-api-config  ConfigMap  Optional: false
    Environment:             <none>
    Mounts:
      /tmp from tmp (rw)
  Volumes:
   tmp:
    Type:          EmptyDir (a temporary directory that shares a pod's lifetime)
    Medium:        
    SizeLimit:     <unset>
  Node-Selectors:  <none>
  Tolerations:     <none>
Conditions:
  Type           Status  Reason
```

## Docker services
```
SERVICE    STATUS                 PORTS
airflow    Up 3 hours (healthy)   0.0.0.0:8081->8080/tcp, [::]:8081->8080/tcp
mlflow     Up 4 hours (healthy)   0.0.0.0:5001->5000/tcp, [::]:5001->5000/tcp
postgres   Up 4 hours (healthy)   5432/tcp
```
