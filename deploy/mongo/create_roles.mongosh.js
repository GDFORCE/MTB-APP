// Usage: mongosh "<admin connection>" --eval 'var DB="uctsm_prod"' create_roles.mongosh.js
// Reads roles.json semantics; run once per environment with that environment's database name.
const spec = JSON.parse(fs.readFileSync(__dirname + "/roles.json", "utf8")).roles;
const target = db.getSiblingDB("admin");
for (const [name, role] of Object.entries(spec)) {
  const privileges = role.privileges.map(p => ({ resource: { db: DB, collection: p.resource.collection }, actions: p.actions }));
  const existing = target.getRole(name);
  if (existing) target.updateRole(name, { privileges, roles: [] });
  else target.createRole({ role: name, privileges, roles: [] });
}
print("roles ready for " + DB + "; create users with: db.createUser({user, pwd: passwordPrompt(), roles: ['uctsm_runtime']})");
