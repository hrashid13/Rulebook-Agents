export async function getOrder(db: any, id: string) {
  return db.query("SELECT * FROM orders WHERE id = $1", [id]);
}
