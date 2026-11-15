function run(input: string, redis: any, model: any) {
  // ruleid: no-eval-typescript
  const a = eval(input);
  // ruleid: no-eval-typescript
  const f = new Function("x", input);
  // ruleid: no-eval-typescript
  const b = window.eval(input);
  // ok: no-eval-typescript
  const c = JSON.parse(input);
  // ok: no-eval-typescript
  const d = redis.eval("return 1", 0);
  // ok: no-eval-typescript
  const e = model.evaluate(input);
  return [a, f, b, c, d, e];
}
