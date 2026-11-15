import logging

log = logging.getLogger(__name__)


def handle(order):
    # ruleid: no-print-debugging-python
    print(order)
    # ruleid: no-print-debugging-python
    print("total:", order.total)
    # ok: no-print-debugging-python
    log.info("order %s", order.id)
    # ok: no-print-debugging-python
    log.debug("order total %s", order.total)
    return order
