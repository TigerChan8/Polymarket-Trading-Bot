import os
import importlib

_clob_module = importlib.util.find_spec("py_clob_client.client")
ClobClient = None
if _clob_module is not None:
    ClobClient = importlib.import_module("py_clob_client.client").ClobClient

if ClobClient is None:
    print("[!] Optional dependency missing: py-clob-client")
    print("[*] Install with: pip install py-clob-client")
else:
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,  # Polygon mainnet
        key=os.getenv("PRIVATE_KEY")
    )

    # Creates new credentials or derives existing ones
    credentials = client.create_or_derive_api_creds()

    print(credentials)
    # {
    #     "apiKey": "550e8400-e29b-41d4-a716-446655440000",
    #     "secret": "base64EncodedSecretString",
    #     "passphrase": "randomPassphraseString"
    # }