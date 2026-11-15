import { Request, Response } from "express";
import { db } from "./db";
import { redis } from "./cache";

const ORDERS_TABLE = "orders";

export async function getOrder(req: Request, res: Response) {
  const rows = await db.query(`SELECT * FROM orders WHERE id = '${req.params.id}'`);
  res.json(rows[0]);
}

export async function countOrders(): Promise<number> {
  const rows = await db.query(`SELECT COUNT(*) AS n FROM ${ORDERS_TABLE}`);
  return rows[0].n;
}

export async function applyDiscount(req: Request, res: Response) {
  const formula = req.body.formula;
  const price = eval(formula);
  res.json({ price });
}

const RELEASE_LOCK = `if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end return 0`;

export async function releaseLock(key: string, owner: string) {
  return redis.eval(RELEASE_LOCK, 1, key, owner);
}
