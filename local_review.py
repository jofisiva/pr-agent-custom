#!/usr/bin/env python
"""
Local PR Review Script for PR-Agent with Ollama LLM

This script runs PR-Agent's review functionality on a local git repository using Ollama LLM,
without requiring a GitHub token or remote PR URL.
"""

import asyncio
import os
import sys
import logging
import datetime
import traceback
import argparse
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import PR-Agent modules with error handling
try:
    from pr_agent.git_providers.local_git_provider import LocalGitProvider
    from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
    from pr_agent.config_loader import get_settings
    from pr_agent.algo.pr_processing import get_pr_diff, retry_with_fallback_models
    from pr_agent.algo.token_handler import TokenHandler
    from pr_agent.git_providers.git_provider import get_main_pr_language
    logger.info("Successfully imported PR-Agent modules")
except ImportError as e:
    logger.error(f"Failed to import PR-Agent modules: {e}")
    print(f"Error importing PR-Agent modules: {e}")
    print("Make sure PR-Agent is installed and in your PYTHONPATH")
    sys.exit(1)

async def run_local_review(target_branch):
    """
    Run a PR review on a local git repository
    
    Args:
        target_branch: The target branch to compare against (e.g. 'main')
    """
    # Set environment variables for Ollama
    os.environ["OLLAMA_CONTEXT_LENGTH"] = "8192"
    logger.info("Set OLLAMA_CONTEXT_LENGTH to 8192")
    
    # Configure settings explicitly
    try:
        settings = get_settings()
        settings.set("CONFIG.CLI_MODE", True)
        settings.set("CONFIG.model", "ollama/llama3.2:1b")
        settings.set("CONFIG.fallback_models", ["ollama/llama3.2:1b"])
        settings.set("CONFIG.custom_model_max_tokens", 8192)
        settings.set("CONFIG.duplicate_examples", True)
        settings.set("CONFIG.custom_reasoning_model", True)
        settings.set("CONFIG.git_provider", "local")
        settings.set("OLLAMA.api_base", "http://localhost:11434")
        logger.info("Successfully configured settings")
        
        # Print current configuration
        print("Current configuration:")
        print(f"Model: {settings.get('CONFIG.model')}")
        print(f"Git Provider: {settings.get('CONFIG.git_provider')}")
        print(f"API Base: {settings.get('OLLAMA.api_base')}")
        print(f"Max tokens: {settings.get('CONFIG.custom_model_max_tokens')}")
    except Exception as e:
        logger.error(f"Error configuring settings: {e}")
        print(f"Error configuring settings: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    # Test Ollama connection
    try:
        import requests
        response = requests.get("http://localhost:11434/api/version")
        print(f"Ollama server status: {response.status_code}")
        print(f"Ollama version: {response.json()}")
    except Exception as e:
        logger.error(f"Error connecting to Ollama: {e}")
        print(f"Error connecting to Ollama: {e}")
        print("Make sure Ollama is running on http://localhost:11434")
        traceback.print_exc()
        sys.exit(1)
    
    # Initialize the local git provider
    try:
        print(f"Initializing LocalGitProvider with target branch: {target_branch}")
        git_provider = LocalGitProvider(target_branch)
        print(f"Analyzing changes between current branch '{git_provider.head_branch_name}' and target branch '{target_branch}'")
        
        # Print diff files for debugging
        diff_files = git_provider.get_diff_files()
        print(f"Found {len(diff_files)} changed files:")
        for file in diff_files:
            print(f"  - {file.filename} ({file.edit_type})")
    except Exception as e:
        logger.error(f"Error initializing local git provider: {e}")
        print(f"Error initializing local git provider: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    # Run the PR review
    try:
        logger.info("Starting PR review with Ollama local LLM...")
        print("Starting PR review with Ollama local LLM...")
        
        # Get main language of the PR
        main_language = get_main_pr_language(
            git_provider.get_languages(), 
            git_provider.get_files()
        )
        print(f"Main language detected: {main_language}")
        
        # Initialize the AI handler
        ai_handler = LiteLLMAIHandler()
        ai_handler.main_pr_language = main_language
        
        # Get PR description and prepare variables
        pr_description, pr_description_files = git_provider.get_pr_description(split_changes_walkthrough=True)
        commit_messages_str = git_provider.get_commit_messages()
        
        # Create token handler with the required parameters
        vars = {
            "title": git_provider.pr.title,
            "branch": git_provider.head_branch_name,  # Using head_branch_name instead of get_pr_branch()
            "description": pr_description,
            "language": main_language,
            "commit_messages_str": commit_messages_str,
            "base_branch": git_provider.target_branch_name,  # Using target_branch_name instead of get_base_branch()
            "pr_author": "Local User",  # Default value for local git provider
            "username": "Local User",  # Default value for local git provider
            "date": datetime.datetime.now().strftime('%Y-%m-%d'),
            "num_max_findings": 5,  # Default value
            "require_estimate_effort_to_review": True,
            "require_security_review": True,
            "is_ai_metadata": False,  # Add missing variable for template
            "extra_instructions": "",  # Add missing variable for template
            "require_can_be_split_review": False,  # Add missing variable for template
            "require_todo_scan": False,  # Add missing variable for template
            "related_tickets": [],  # Add missing variable for template
            "require_score": False,  # Add missing variable for template
        }
        
        # Get system and user prompts from settings
        from jinja2 import Environment, StrictUndefined
        environment = Environment(undefined=StrictUndefined)
        
        try:
            # Get prompts from settings
            system_prompt = environment.from_string(settings.pr_review_prompt.system).render(vars)
            user_prompt = environment.from_string(settings.pr_review_prompt.user).render(vars)
            
            # Initialize token handler with the required parameters
            token_handler = TokenHandler(
                pr=git_provider.pr,
                vars=vars,
                system=system_prompt,
                user=user_prompt
            )
            
            # Get PR diff with all required parameters
            patches_diff = get_pr_diff(
                git_provider=git_provider,
                token_handler=token_handler,
                model=settings.config.model,
                add_line_numbers_to_hunks=False,
                disable_extra_lines=False
            )
            
            # Update diff in variables
            vars["diff"] = patches_diff
            
            # Re-render prompts with the diff included
            system_prompt = environment.from_string(settings.pr_review_prompt.system).render(vars)
            user_prompt = environment.from_string(settings.pr_review_prompt.user).render(vars)
            
            print(f"Successfully loaded prompts. System prompt: {len(system_prompt)} chars, User prompt: {len(user_prompt)} chars")
            
            # Generate the review
            logger.info("Generating review with Ollama LLM...")
            print("Generating review with Ollama LLM...")
            
            # Use AI handler to generate the review
            response = await ai_handler.chat_completion(
                model=settings.config.model,
                system=system_prompt,
                user=user_prompt,
                temperature=0.2
            )
            
            # Extract content from response - LiteLLMAIHandler.chat_completion returns a tuple
            # The first element of the tuple is the actual response content
            if isinstance(response, tuple) and len(response) > 0:
                review_content = response[0]
            else:
                review_content = str(response)
            logger.info(f"Review generated successfully: {len(review_content)} chars")
            print(f"Review generated successfully: {len(review_content)} chars")
            
            # Save the review to a file
            git_provider.publish_comment(review_content)
            print(f"Review saved to {git_provider.review_path}")
            return review_content
        except Exception as e:
            logger.error(f"Error generating prompts: {e}")
            print(f"Error generating prompts: {e}")
            
            # Try with a simpler fallback prompt
            try:
                logger.info("Using fallback simple prompt...")
                print("Using fallback simple prompt...")
                
                # Create a simple diff manually instead of using get_pr_diff
                if 'diff' not in vars:
                    # Get diff files directly from git_provider
                    diff_files = git_provider.get_diff_files()
                    patches_diff = ""
                    for file_patch in diff_files:
                        patches_diff += f"File: {file_patch.filename}\n"
                        patches_diff += f"Status: {file_patch.edit_type.name}\n"
                        patches_diff += f"{file_patch.patch}\n\n"
                    vars["diff"] = patches_diff
                
                # Create simple prompts
                system_prompt = "You are a helpful code reviewer. Analyze the PR and provide constructive feedback."
                user_prompt = f"Review the following PR:\n\nTitle: {vars['title']}\n\nDescription: {vars['description']}\n\nDiff:\n{vars['diff']}"
                
                # Call the AI handler directly
                try:
                    response = await ai_handler.chat_completion(
                        model=settings.config.model,
                        system=system_prompt,
                        user=user_prompt,
                        temperature=0.2
                    )
                    
                    # Extract content from response - LiteLLMAIHandler.chat_completion returns a tuple
                    # The first element of the tuple is the actual response content
                    if isinstance(response, tuple) and len(response) > 0:
                        review_content = response[0]
                    else:
                        review_content = str(response)
                    
                    # Save the review to a file
                    git_provider.publish_comment(review_content)
                    print(f"Review saved to {git_provider.review_path}")
                    return review_content
                except Exception as e:
                    logger.error(f"Error generating fallback prompts: {e}")
                    print(f"Error generating fallback prompts: {e}")
                    traceback.print_exc()
                    return None
            except Exception as e:
                logger.error(f"Error in fallback prompt: {e}")
                print(f"Error in fallback prompt: {e}")
                traceback.print_exc()
                return None
        
        return None
    except Exception as e:
        logger.error(f"Error during review: {e}")
        print(f"Error during review: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PR-Agent review on a local git repository")
    parser.add_argument("--target-branch", required=True, help="Target branch to compare against (e.g. 'main')")
    parser.add_argument("--repo-path", default=".", help="Path to the git repository (default: current directory)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Change to the repository directory if specified
    if args.repo_path != ".":
        os.chdir(args.repo_path)
    
    logger.info(f"Target branch: {args.target_branch}")
    logger.info(f"Repository path: {os.getcwd()}")
    
    asyncio.run(run_local_review(args.target_branch))
