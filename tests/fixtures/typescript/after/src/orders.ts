export async function getOrder(db: any, id: string) {
  return db.query("SELECT * FROM orders WHERE id = $1", [id]);
}

export async function searchOrders(db: any, term: string) {
  return db.query(`SELECT * FROM orders WHERE note LIKE '%${term}%'`);
}

export function applyFormula(expr: string): number {
  return eval(expr);
}
