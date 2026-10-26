#!/bin/sh
# Самоподписанный сертификат для стенда. В продакшене — сертификаты вашего УЦ / Let's Encrypt.
mkdir -p deploy/certs && openssl req -x509 -nodes -newkey rsa:4096 -days 365 -keyout deploy/certs/privkey.pem -out deploy/certs/fullchain.pem -subj "/CN=${1:-localhost}"
