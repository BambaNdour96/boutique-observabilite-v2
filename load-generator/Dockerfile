FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ "setuptools==69.5.1"
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
COPY locustfile.py .
EXPOSE 8089
CMD ["locust","--host","http://frontend-service:5000","--headless","--users","10","--spawn-rate","2"]
