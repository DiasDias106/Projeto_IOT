// Node-RED: HTTPS on port 1880 and editor login (design-plan.md, section 1).
// Sensitive values come from .env through docker-compose.yml.
const fs = require("fs");
const path = require("path");

// Credentials of the MQTT and MySQL config nodes are only references to environment variables
// (${MQTT_USER}, ${DB_PASSWORD}, ...), which Node-RED resolves at start. flows_cred.json is not
// committed and is encrypted with a per-person secret, so a fresh clone (or a new .env) would
// leave them empty. It is rewritten here on every start; Node-RED encrypts it again on deploy.
// No secret is written: only the variable names.
(function writeCredentialRefs() {
    const flowsPath = path.join(__dirname, "flows.json");
    const credPath = path.join(__dirname, "flows_cred.json");
    const refs = {
        "mqtt-broker": { user: "${MQTT_USER}", password: "${MQTT_PASSWORD}" },
        "Stackhero-MySQL-Server": { user: "${DB_USER}", password: "${DB_PASSWORD}" },
    };
    try {
        const creds = {};
        for (const node of JSON.parse(fs.readFileSync(flowsPath, "utf8"))) {
            if (refs[node.type]) creds[node.id] = refs[node.type];
        }
        fs.writeFileSync(credPath, JSON.stringify(creds, null, 4));
    } catch (err) {
        console.error("Could not write flows_cred.json:", err.message);
    }
})();

module.exports = {
    uiPort: 1880,
    flowFile: "flows.json",
    flowFilePretty: true,

    // Encrypts flows_cred.json on deploy (not committed)
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
