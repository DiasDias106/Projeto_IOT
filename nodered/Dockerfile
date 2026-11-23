FROM nodered/node-red:4.0.9

# Extra nodes: dashboard, MySQL and map (design-plan.md, section 1).
# MySQL: node-red-node-mysql does not support TLS, which MySQL requires. stackhero-mysql (mysql2) does,
# and trusts the group CA through NODE_EXTRA_CA_CERTS (see docker-compose.yml).
RUN npm install --no-audit --no-fund \
    node-red-dashboard@3.6.6 \
    node-red-contrib-stackhero-mysql@1.0.6 \
    node-red-contrib-web-worldmap@5.9.2
