FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY proxy_params /etc/nginx/proxy_params
COPY index.html /usr/share/nginx/html/index.html
RUN mkdir -p /tmp/client_temp /tmp/proxy_temp && chown -R nginx:nginx /tmp/client_temp /tmp/proxy_temp /var/cache/nginx
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s --start-period=5s --retries=3 CMD ["wget", "-q", "-O", "-", "http://127.0.0.1:8080/health"]
