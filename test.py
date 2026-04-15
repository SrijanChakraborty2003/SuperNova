import asyncio
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

async def main():
    ## 1. Setup the Model with reasoning/thinking enabled
    model = ChatOllama(
        model="gemma4:e4b", 
        base_url="http://127.0.0.1:11434",
        # Some models require specific flags to show their 'thinking' tags
        additional_kwargs={"num_predict": 1024} 
    )

    # 2. Setup MCP Client - Ensure these paths match your actual PC folders
    client = MultiServerMCPClient(
        connections={
            "chrome-launcher": {
                "command": "python",
                "args": ["C:/Users/User/OneDrive/Documents/GitHub/SuperNova/chrome-mcp.py"],
                "transport": "stdio"
            },
            "windows-mcp":{
                "command": "python",
                "args": ["C:/Users/User/OneDrive/Documents/GitHub/SuperNova/windows-mcp.py"],
                "transport": "stdio"
            },
            "cmd-mcp":{
                "command": "python",
                "args": ["C:/Users/User/OneDrive/Documents/GitHub/SuperNova/cmd-mcp.py"],
                "transport": "stdio"
            }
        }
    )

    # 3. Initialize tools
    tools = await client.get_tools()
    print(f"✅ Total tools found: {len(tools)}")
    
    # Define your system prompt
    system_prompt = (
        "You are a helpful assistant with access to local tools. "
        "Use 'open_chrome' for web requests, 'run_command' for terminal tasks, "
        "and 'create_file' or 'list_directory' for filesystem tasks."
    )
    
    # FIXED: Using 'prompt' instead of 'state_modifier' 
    # and ensuring we handle the tool-calling agent correctly.
    agent = create_react_agent(model, tools, prompt=system_prompt)

    print("--- 🤖 MCP Agent Ready (Type 'exit' or 'quit' to stop) ---")
# 4. Interactive Loop with Thought Display
    while True:
        user_input = input("\nUser: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        inputs = {"messages": [("user", user_input)]}
        
        async for chunk in agent.astream(inputs, stream_mode="values"):
            last_message = chunk["messages"][-1]
            
            if last_message.type == "ai":
                # CHECK FOR THINKING: 
                # LangChain often stores 'thinking' in additional_kwargs or at the start of content
                reasoning = last_message.additional_kwargs.get("reasoning_content", "")
                
                if reasoning:
                    print(f"\n🧠 THOUGHT:\n{reasoning}\n")
                
                # Print the final response or tool-call attempt
                last_message.pretty_print()
            
            elif last_message.type == "tool":
                # Print the result of the tool execution
                last_message.pretty_print()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping...")