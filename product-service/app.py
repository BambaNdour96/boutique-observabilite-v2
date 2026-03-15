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

OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_NAME = "product-service"

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

from flask import Flask, jsonify
CATALOG_FAILURE = os.getenv("CATALOG_FAILURE","false").lower()=="true"
FAILING_PRODUCT_ID = "3"
request_counter = meter.create_counter("product_requests_total")
error_counter   = meter.create_counter("product_errors_total")
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()
PRODUCTS = [
  {"id":"1","name":"Laptop Pro X1","price":999.99,"stock":10,"image":"laptop.jpg","category":"Ordinateurs","description":"Intel i7 16Go RAM SSD 512Go"},
  {"id":"2","name":"Wireless Mouse M200","price":29.99,"stock":50,"image":"mouse.jpg","category":"Accessoires","description":"Souris ergonomique sans fil autonomie 12 mois"},
  {"id":"3","name":"Mechanical Keyboard","price":79.99,"stock":30,"image":"keyboard.jpg","category":"Accessoires","description":"Clavier mecanique RGB switches Cherry MX"},
  {"id":"4","name":"USB-C Hub 7-en-1","price":49.99,"stock":25,"image":"hub.jpg","category":"Accessoires","description":"HDMI 4K USB 3.0 x3 SD card charging 100W"},
  {"id":"5","name":"Monitor 4K 27p","price":449.99,"stock":8,"image":"monitor.jpg","category":"Ecrans","description":"Dalle IPS 4K 144Hz HDR400 temps reponse 1ms"},
  {"id":"6","name":"Casque Audio Pro","price":149.99,"stock":20,"image":"headphones.jpg","category":"Audio","description":"Casque sans fil reduction de bruit active"},
  {"id":"7","name":"Webcam HD 1080p","price":89.99,"stock":15,"image":"webcam.jpg","category":"Video","description":"Webcam Full HD autofocus micro integre"},
  {"id":"8","name":"SSD NVMe 1To","price":119.99,"stock":35,"image":"ssd.jpg","category":"Stockage","description":"SSD NVMe PCIe 4.0 lecture 7000 Mo/s"},
]
@app.route("/health")
def health(): return jsonify({"status":"ok","service":SERVICE_NAME})
@app.route("/products")
def list_products():
    request_counter.add(1,{"endpoint":"list"})
    logger.info("Listing all products — %d items", len(PRODUCTS))
    return jsonify(PRODUCTS)
@app.route("/products/<pid>")
def get_product(pid):
    request_counter.add(1,{"endpoint":"get"})
    if CATALOG_FAILURE and pid==FAILING_PRODUCT_ID:
        error_counter.add(1,{"reason":"catalog_failure"})
        logger.error("context deadline exceeded for product %s — catalog failure injected", pid)
        time.sleep(5)
        return jsonify({"error":"context deadline exceeded"}),504
    p = next((x for x in PRODUCTS if x["id"]==pid),None)
    if not p:
        logger.warning("Product %s not found", pid)
        return jsonify({"error":"product not found"}),404
    logger.info("Product %s retrieved successfully", pid)
    return jsonify(p)
if __name__=="__main__": app.run(host="0.0.0.0",port=5001)
