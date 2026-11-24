FROM node:22-alpine AS builder

WORKDIR /app
COPY frontend/package.json ./package.json
RUN npm install
COPY frontend .
RUN npm run build

FROM nginx:1.27-alpine

ENV API_UPSTREAM=api:8000

COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
