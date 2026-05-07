#!/usr/bin/env bash
set -euo pipefail

IMAGE="${OPSFACTORY_LOCAL_PLATFORM_IMAGE:-registry.k8s.io/pause:3.10.1}"

create_platform() {
  local namespace="$1"
  shift
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
  for service in "$@"; do
    cat <<YAML | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${service}
  namespace: ${namespace}
  labels:
    app: ${service}
    name: ${service}
    service: ${service}
    opsfactory.ai/platform: ${namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${service}
  template:
    metadata:
      labels:
        app: ${service}
        name: ${service}
        service: ${service}
        opsfactory.ai/platform: ${namespace}
    spec:
      containers:
        - name: ${service}
          image: ${IMAGE}
          imagePullPolicy: IfNotPresent
YAML
    cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: ${service}
  namespace: ${namespace}
  labels:
    app: ${service}
    name: ${service}
    service: ${service}
    opsfactory.ai/platform: ${namespace}
spec:
  selector:
    app: ${service}
  ports:
    - name: http
      port: 80
      targetPort: 80
YAML
  done
}

create_platform sock-shop \
  front-end carts catalogue orders payment shipping user queue-master rabbitmq mongodb mysql session-db

create_platform online-shop \
  frontend productcatalogservice cartservice checkoutservice paymentservice shippingservice emailservice recommendationservice currencyservice adservice

create_platform train-ticket \
  ts-ui-dashboard ts-basic-service ts-train-service ts-travel-service ts-preserve-service ts-order-service ts-contact-service ts-notification-service \
  ts-seat-service ts-config-service ts-station-service ts-price-service ts-auth-service ts-user-service ts-executor-service ts-route-service \
  ts-route-plan-service ts-assurance-service ts-cancel-service ts-food-service ts-consign-service ts-travel2-service ts-inside-pay-service \
  ts-news-service ts-voucher-service ts-admin-basic-service

kubectl get pods -n sock-shop
kubectl get pods -n online-shop
kubectl get pods -n train-ticket
