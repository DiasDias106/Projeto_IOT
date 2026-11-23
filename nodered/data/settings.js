// Node-RED: HTTPS on port 1880 and editor login (design-plan.md, section 1).
// Sensitive values come from .env through docker-compose.yml.
const fs = require("fs");

module.exports = {
    uiPort: 1880,
    flowFile: "flows.json",
    flowFilePretty: true,

    // Encrypts flows_cred.json (not committed)
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

    // Dashboard at https://localhost:1880/ui
    ui: { path: "ui" },

    // Available in function nodes as env.get("...") or in fields as ${...}
    functionGlobalContext: {},

    logging: { console: { level: "info", metrics: false, audit: false } },
    editorTheme: { projects: { enabled: false } },
};
