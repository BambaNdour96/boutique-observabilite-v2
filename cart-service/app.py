import os, time, logging
from opentelemetry import trace
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
import redis
from flask import Flask, jsonify, request

OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_NAME = "cart-service"

resource = Resource.create({"service.name": SERVICE_NAME})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(SERVICE_NAME)
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
set_logger_provider(logger_provider)
otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(SERVICE_NAME)
logger.addHandler(otel_handler)
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True))
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
set_meter_provider(meter_provider)
meter = meter_provider.get_meter(SERVICE_NAME)

CART_FAILURE = os.getenv("CART_FAILURE","false").lower()=="true"
REDIS_HOST   = os.getenv("REDIS_HOST","redis")
REDIS_PORT   = int(os.getenv("REDIS_PORT","6379"))
request_counter = meter.create_counter("cart_requests_total")
error_counter   = meter.create_counter("cart_errors_total")
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

def get_redis():
    if CART_FAILURE:
        raise ConnectionError("Wasn't able to connect to redis — cart failure injected")
    return redis.Redis(host=REDIS_HOST,port=REDIS_PORT,decode_responses=True)

@app.route("/health")
def health(): return jsonify({"status":"ok","service":SERVICE_NAME})

@app.route("/cart/<uid>")
def get_cart(uid):
    request_counter.add(1,{"endpoint":"get"})
    try:
        r=get_redis(); items=r.hgetall(f"cart:{uid}")
        logger.info("Cart retrieved for user %s", uid)
        return jsonify({"user_id":uid,"items":items})
    except ConnectionError as e:
        error_counter.add(1,{"reason":"cart_failure"})
        logger.error("Error status code FailedPrecondition — Can't access cart storage: %s", e)
        return jsonify({"error":"cart storage unavailable"}),503

@app.route("/cart/add",methods=["POST"])
def add_to_cart():
    request_counter.add(1,{"endpoint":"add"})
    data=request.get_json()
    uid=data.get("user_id","anonymous")
    pid=data.get("product_id")
    qty=data.get("quantity",1)
    try:
        r=get_redis(); r.hset(f"cart:{uid}",pid,qty)
        logger.info("Added product %s to cart for user %s", pid, uid)
        return jsonify({"status":"added","user_id":uid,"product_id":pid})
    except ConnectionError as e:
        error_counter.add(1,{"reason":"cart_failure"})
        logger.error("Error status code FailedPrecondition — Can't access cart storage: %s", e)
        return jsonify({"error":"cart storage unavailable"}),503

@app.route("/cart/<uid>/clear",methods=["DELETE"])
def clear_cart(uid):
    try:
        r=get_redis(); r.delete(f"cart:{uid}")
        logger.info("Cart cleared for user %s", uid)
        return jsonify({"status":"cleared"})
    except ConnectionError as e:
        logger.error("Error status code FailedPrecondition — Can't access cart storage: %s", e)
        return jsonify({"error":"cart storage unavailable"}),503

if __name__=="__main__": app.run(host="0.0.0.0",port=5002)
