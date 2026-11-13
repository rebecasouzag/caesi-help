"""Cliente do Supabase."""
import os
from functools import lru_cache

from supabase import Client, create_client

@lru_cache
def get_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
