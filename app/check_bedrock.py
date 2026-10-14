import boto3
from dotenv import load_dotenv

load_dotenv()
print("account:", boto3.client("sts").get_caller_identity()["Account"])

rt = boto3.client("bedrock-runtime", region_name="us-east-1")
tests = [
    "amazon.nova-micro-v1:0",
    "us.amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
    "amazon.titan-embed-text-v2:0",
]

for model in tests:
    try:
        if "embed" in model:
            rt.invoke_model(modelId=model, body='{"inputText": "hello"}')
        else:
            rt.converse(modelId=model, messages=[{"role": "user", "content": [{"text": "hi"}]}])
        print("OK  ", model)
    except Exception as e:
        print("FAIL", model, "->", e)