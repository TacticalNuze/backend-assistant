from langfuse import Langfuse
from dotenv import load_dotenv

load_dotenv()
# Initialize Langfuse client
langfuse = Langfuse()

# Get production prompt
prompt = langfuse.get_prompt("run-vector-rag")

# Get by label
# You can use as many labels as you'd like to identify different deployment targets
prompt = langfuse.get_prompt("run-vector-rag", label="en")

# Get by version number, usually not recommended as it requires code changes to deploy new prompt versions
langfuse.get_prompt("run-vector-rag", version=1)
print(prompt)