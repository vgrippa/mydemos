FROM node:18-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG FOODADVISOR_REF=master
RUN git clone --depth 1 --branch ${FOODADVISOR_REF} https://github.com/strapi/foodadvisor.git /tmp/foodadvisor \
    && mv /tmp/foodadvisor/api /app/api \
    && rm -rf /tmp/foodadvisor

WORKDIR /app/api

RUN yarn install --ignore-engines --network-timeout 600000 \
    && yarn add pg@8.11.3 --ignore-engines --network-timeout 600000

COPY config/database.js /app/api/config/database.js

RUN yarn build

EXPOSE 1337

CMD ["sh", "-c", "yarn strapi start"]
