// Node-RED: HTTPS na porta 1880 e login no editor (design-plan.md, secção 1).
// Os valores sensíveis vêm do .env através do docker-compose.yml.
const fs = require("fs");

module.exports = {
    uiPort: 1880,
    flowFile: "flows.json",
    flowFilePretty: true,

    // Cifra o flows_cred.json (que não é versionado)
    credentialSecret: process.env.NODERED_CREDENTIAL_SECRET,

    https: {
        key: fs.readFileSync("/certs/server.key"),
        cert: fs.readFileSync("/certs/server.crt"),
    },
    requireHttps: true,

    adminAuth: {
        type: "credentials",
        users: [{
            username: process.env.NODERED_ADMIN_USER || "admin",
            password: (process.env.NODERED_ADMIN_PASSWORD_HASH || "").replace(/^'|'$/g, ""),
            permissions: "*",
        }],
    },

    // Dashboard em https://localhost:1880/ui
    ui: { path: "ui" },

    // Disponível nos nós function como env.get("...") ou nos campos com ${...}
    functionGlobalContext: {},

    logging: { console: { level: "info", metrics: false, audit: false } },
    editorTheme: { projects: { enabled: false } },
};
