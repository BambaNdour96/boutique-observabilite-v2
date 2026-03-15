import os, time, logging
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
import uuid, requests as req
from flask import Flask, jsonify, request

OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_NAME = "order-service"

resource = Resource.create({"service.name": SERVICE_NAME})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(SERVICE_NAME)
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
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
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True))
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
set_meter_provider(meter_provider)
meter = meter_provider.get_meter(SERVICE_NAME)

PAYMENT_FAILURE = os.getenv("PAYMENT_FAILURE","false").lower()=="true"
CART_SERVICE    = os.getenv("CART_SERVICE","http://cart-service:5002")
request_counter = meter.create_counter("order_requests_total")
error_counter   = meter.create_counter("order_errors_total")
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

@app.route("/health")
def health(): return jsonify({"status":"ok","service":SERVICE_NAME})

@app.route("/order/checkout",methods=["POST"])
def checkout():
    request_counter.add(1,{"endpoint":"checkout"})
    data   = request.get_json()
    uid    = data.get("user_id","anonymous")
    oid    = str(uuid.uuid4())[:8].upper()
    logger.info("Checkout initiated for user %s order %s", uid, oid)
    if PAYMENT_FAILURE:
        error_counter.add(1,{"reason":"payment_failure"})
        logger.error("Payment rejected for order %s — invalid card details, payment failure injected", oid)
        return jsonify({"error":"Payment rejected — invalid card details"}),402
    try:
        r = req.delete(f"{CART_SERVICE}/cart/{uid}/clear", timeout=3)
    except Exception as e:
        logger.warning("Could not clear cart for user %s: %s", uid, e)
    logger.info("Order %s confirmed for user %s", oid, uid)
    return jsonify({"status":"confirmed","order_id":oid,"user_id":uid})

@app.route("/order/<oid>")
def get_order(oid):
    logger.info("Order %s status requested", oid)
    return jsonify({"order_id":oid,"status":"confirmed"})

if __name__=="__main__": app.run(host="0.0.0.0",port=5003)
