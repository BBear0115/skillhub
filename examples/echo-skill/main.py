def handle_tool(context):
    text = context["arguments"].get("text", "")
    return {
        "content": [
            {
                "type": "text",
                "text": f"echo:{text}",
            }
        ],
        "isError": False,
    }
