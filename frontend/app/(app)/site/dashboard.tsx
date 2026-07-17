// Site / Hospital OPERATIONAL dashboard.
//
// Deliberately separate from organization administration: a `site` account
// lands on the de-identified operational dashboard (trials, masked subjects,
// visit workload via GET /api/site/dashboard), while the governed org-admin
// console (/(app)/org-admin/site — members, ownership transfer, delegation,
// audit) stays behind the explicit Organization Oversight entry for org
// administrators only.
export { default } from "../pi/dashboard";
