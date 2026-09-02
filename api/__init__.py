"""LangGraph Platform-compatible serverless API for the Disc-Orchestrator agent.

Runs on AWS Lambda (Docker image + Lambda Web Adapter + Function URL) and
implements the subset of the LangGraph Platform REST API that DiscOrch clients
(run_cloud.py, resume_thread.py, the frontend, UAT monitors) actually call.
LangGraph Cloud hosting keeps working unchanged via langgraph.json.
"""
