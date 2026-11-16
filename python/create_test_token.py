#!/usr/bin/env python3
"""
Helper script to create API tokens for testing

This script calls the Horux backend API to create API tokens with different permissions.
Requires authentication with the backend (session cookie or JWT).

Usage:
    python create_test_token.py --help
"""

import argparse
import sys

import requests


def create_api_token(backend_url, session_cookie, name, description, role_id, expires_in_days=None):
    """Create an API token via the backend API"""

    url = f"{backend_url}/api/v1/user/api-tokens"

    payload = {"name": name, "description": description, "roleId": role_id}

    if expires_in_days:
        payload["expiresInDays"] = expires_in_days

    headers = {"Content-Type": "application/json"}

    cookies = {}
    if session_cookie:
        cookies["connect.sid"] = session_cookie

    try:
        response = requests.post(url, json=payload, headers=headers, cookies=cookies)

        if response.status_code == 201:
            data = response.json()
            return {
                "success": True,
                "token": data.get("plainTextToken"),
                "token_id": data.get("token", {}).get("uuid"),
                "name": data.get("token", {}).get("name"),
                "role_id": data.get("token", {}).get("role_id"),
            }
        else:
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def list_roles(backend_url, session_cookie):
    """List available roles"""

    url = f"{backend_url}/api/v1/user/roles"

    headers = {"Content-Type": "application/json"}

    cookies = {}
    if session_cookie:
        cookies["connect.sid"] = session_cookie

    try:
        response = requests.get(url, headers=headers, cookies=cookies)

        if response.status_code == 200:
            roles = response.json()
            return {"success": True, "roles": roles}
        else:
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Create API tokens for Horux testing")

    parser.add_argument(
        "--backend-url",
        default="http://localhost:3000",
        help="Backend API URL (default: http://localhost:3000)",
    )
    parser.add_argument(
        "--session-cookie",
        required=False,
        help="Session cookie value (connect.sid) for authentication",
    )
    parser.add_argument("--list-roles", action="store_true", help="List available roles")
    parser.add_argument("--name", default="Test Ingestion Token", help="Token name")
    parser.add_argument(
        "--description", default="API token for testing data ingestion", help="Token description"
    )
    parser.add_argument(
        "--role-id", required=False, help="Role UUID (use --list-roles to see available roles)"
    )
    parser.add_argument(
        "--expires-in-days", type=int, default=30, help="Token expiration in days (default: 30)"
    )

    args = parser.parse_args()

    print("Horux API Token Creator")
    print("=" * 50)

    # List roles if requested
    if args.list_roles:
        print("\nFetching available roles...")
        result = list_roles(args.backend_url, args.session_cookie)

        if result["success"]:
            print("\nAvailable Roles:")
            print("-" * 50)
            for role in result["roles"]:
                print(f"  UUID: {role.get('uuid')}")
                print(f"  Name: {role.get('name')}")
                print(f"  Description: {role.get('description', 'N/A')}")
                print(f"  Permissions: {len(role.get('permissions', []))} permissions")
                print("-" * 50)
        else:
            print(f"Error: {result['error']}")
            sys.exit(1)
        return

    # Create token
    if not args.role_id:
        print("\nError: --role-id is required to create a token")
        print("Use --list-roles to see available roles")
        sys.exit(1)

    if not args.session_cookie:
        print("\nWarning: No session cookie provided")
        print("You must be authenticated with the backend to create tokens")
        print("\nTo get your session cookie:")
        print("  1. Log in to the Horux console in your browser")
        print("  2. Open Developer Tools (F12)")
        print("  3. Go to Application/Storage → Cookies")
        print("  4. Copy the value of 'connect.sid'")
        print("  5. Run: python create_test_token.py --session-cookie <value> --role-id <uuid>")
        sys.exit(1)

    print("\nCreating API token...")
    print(f"  Name: {args.name}")
    print(f"  Description: {args.description}")
    print(f"  Role ID: {args.role_id}")
    print(f"  Expires in: {args.expires_in_days} days")

    result = create_api_token(
        args.backend_url,
        args.session_cookie,
        args.name,
        args.description,
        args.role_id,
        args.expires_in_days,
    )

    if result["success"]:
        print("\n✓ Token created successfully!")
        print("=" * 50)
        print(f"Token ID: {result['token_id']}")
        print(f"Name: {result['name']}")
        print(f"Role ID: {result['role_id']}")
        print("\n⚠️  IMPORTANT: Save this token - it will only be shown once!")
        print("=" * 50)
        print(f"\nAPI Token: {result['token']}")
        print("=" * 50)
        print("\nUse this token to test ingestion:")
        print(f"  python test_auth.py {result['token']}")
    else:
        print(f"\n✗ Failed to create token: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
