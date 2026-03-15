import os, logging, uuid
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from opentelemetry import trace
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import set_meter_provider
import requests as req

OTLP_ENDPOINT    = os.getenv("OTLP_ENDPOINT","http://otel-collector:4317")
SERVICE_NAME     = "frontend-service"
PRODUCT_SERVICE  = os.getenv("PRODUCT_SERVICE","http://product-service:5001")
CART_SERVICE     = os.getenv("CART_SERVICE","http://cart-service:5002")
ORDER_SERVICE    = os.getenv("ORDER_SERVICE","http://order-service:5003")

resource = Resource.create({"service.name": SERVICE_NAME})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT,insecure=True)))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(SERVICE_NAME)
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT,insecure=True)))
set_logger_provider(logger_provider)
otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)

class TraceIdFilter(logging.Filter):
    def filter(self, record):
        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx and ctx.trace_id else "0" * 32
        return True

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s traceId=%(trace_id)s %(message)s"))
trace_filter = TraceIdFilter()
handler.addFilter(trace_filter)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = []
root_logger.addHandler(handler)
for name in ["werkzeug", "flask"]:
    lg = logging.getLogger(name)
    lg.handlers = []
    lg.addHandler(handler)
    lg.propagate = False
logger = logging.getLogger(SERVICE_NAME)
logger.addHandler(otel_handler)
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=OTLP_ENDPOINT,insecure=True))
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
set_meter_provider(meter_provider)
meter = meter_provider.get_meter(SERVICE_NAME)
page_counter     = meter.create_counter("frontend_page_views_total")
checkout_counter = meter.create_counter("frontend_checkout_total")
error_counter    = meter.create_counter("frontend_errors_total")

app = Flask(__name__)
app.secret_key = "techshop-secret-2024"
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

@app.route("/health")
def health(): return jsonify({"status":"ok","service":SERVICE_NAME})

@app.route("/")
def index():
    page_counter.add(1,{"page":"home"})
    try:
        products = req.get(f"{PRODUCT_SERVICE}/products", timeout=5).json()
    except Exception as e:
        logger.error("Failed to fetch products: %s", e)
        products = []
    uid = session.get("user_id", str(uuid.uuid4())[:8])
    session["user_id"] = uid
    cart_count = 0
    try:
        cart = req.get(f"{CART_SERVICE}/cart/{uid}", timeout=3).json()
        cart_count = len(cart.get("items",{}))
    except Exception:
        pass
    logger.info("Home page loaded — %d products displayed", len(products))
    return render_template("index.html", products=products, cart_count=cart_count)

@app.route("/product/<pid>")
def product_detail(pid):
    page_counter.add(1,{"page":"product"})
    try:
        product = req.get(f"{PRODUCT_SERVICE}/products/{pid}", timeout=5).json()
        if "error" in product:
            logger.error("Product %s error: %s", pid, product["error"])
            error_counter.add(1,{"page":"product","reason":product["error"]})
            return render_template("error.html", message=product["error"]), 504
    except Exception as e:
        logger.error("Failed to fetch product %s: %s", pid, e)
        return render_template("error.html", message="Service indisponible"), 503
    uid = session.get("user_id", str(uuid.uuid4())[:8])
    session["user_id"] = uid
    logger.info("Product detail page for %s loaded", pid)
    return render_template("product.html", product=product)

@app.route("/cart/add", methods=["POST"])
def add_to_cart():
    uid = session.get("user_id", str(uuid.uuid4())[:8])
    session["user_id"] = uid
    pid = request.form.get("product_id")
    qty = int(request.form.get("quantity", 1))
    try:
        r = req.post(f"{CART_SERVICE}/cart/add",
                     json={"user_id":uid,"product_id":pid,"quantity":qty}, timeout=3)
        if r.status_code != 200:
            logger.error("Cart add failed for user %s product %s: %s", uid, pid, r.text)
            error_counter.add(1,{"page":"cart","reason":"cart_failure"})
            return render_template("error.html", message="Panier indisponible — " + r.json().get("error","")), 503
        logger.info("Product %s added to cart for user %s", pid, uid)
    except Exception as e:
        logger.error("Cart service unreachable: %s", e)
        return render_template("error.html", message="Service panier indisponible"), 503
    return redirect(url_for("cart"))

@app.route("/cart")
def cart():
    page_counter.add(1,{"page":"cart"})
    uid = session.get("user_id", str(uuid.uuid4())[:8])
    session["user_id"] = uid
    items = {}
    total = 0.0
    try:
        cart_data = req.get(f"{CART_SERVICE}/cart/{uid}", timeout=3).json()
        raw = cart_data.get("items", {})
        products = req.get(f"{PRODUCT_SERVICE}/products", timeout=5).json()
        pmap = {p["id"]: p for p in products}
        for pid, qty in raw.items():
            if pid in pmap:
                p = pmap[pid]
                subtotal = p["price"] * int(qty)
                total += subtotal
                items[pid] = {"name":p["name"],"price":p["price"],"qty":int(qty),"subtotal":subtotal,"image":p["image"]}
    except Exception as e:
        logger.error("Failed to load cart for user %s: %s", uid, e)
        error_counter.add(1,{"page":"cart","reason":"load_failed"})
    logger.info("Cart page loaded for user %s — %d items total %.2f", uid, len(items), total)
    return render_template("cart.html", items=items, total=total, cart_count=len(items))

@app.route("/checkout", methods=["GET","POST"])
def checkout():
    uid = session.get("user_id", str(uuid.uuid4())[:8])
    session["user_id"] = uid
    if request.method == "GET":
        page_counter.add(1,{"page":"checkout"})
        return render_template("checkout.html", cart_count=0)
    checkout_counter.add(1,{"step":"submit"})
    try:
        r = req.post(f"{ORDER_SERVICE}/order/checkout",
                     json={"user_id":uid}, timeout=10)
        if r.status_code != 200:
            data = r.json()
            logger.error("Checkout failed for user %s: %s", uid, data.get("error",""))
            error_counter.add(1,{"page":"checkout","reason":"payment_failure"})
            return render_template("error.html", message="Paiement refuse — " + data.get("error","")), r.status_code
        order = r.json()
        logger.info("Checkout successful for user %s order %s", uid, order.get("order_id"))
        return render_template("confirmation.html", order=order, cart_count=0)
    except Exception as e:
        logger.error("Order service unreachable for user %s: %s", uid, e)
        return render_template("error.html", message="Service commande indisponible"), 503

if __name__=="__main__": app.run(host="0.0.0.0", port=5000)
