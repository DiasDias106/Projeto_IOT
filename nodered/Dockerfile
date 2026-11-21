FROM nodered/node-red:4.0.9

# Nós extra: dashboard, MySQL e mapa (design-plan.md, secção 1).
# MySQL: o node-red-node-mysql não suporta TLS e o MySQL exige-o. O stackhero-mysql (mysql2) suporta,
# e confia na CA do grupo através de NODE_EXTRA_CA_CERTS (ver docker-compose.yml).
RUN npm install --no-audit --no-fund \
    node-red-dashboard@3.6.6 \
    node-red-contrib-stackhero-mysql@1.0.6 \
    node-red-contrib-web-worldmap@5.9.2
