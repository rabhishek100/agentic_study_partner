from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_local_runner_is_pinned_and_never_names_production(self) -> None:
        script = self.read("scripts/local.sh")
        self.assertIn('SUPABASE_VERSION="${SUPABASE_VERSION:-2.109.1}"', script)
        self.assertIn("studio,logflare,edge-runtime,vector,imgproxy", script)
        self.assertNotIn("usuulfckhbeypjxwjpfn", script)
        self.assertIn("DOCKER_SUPABASE_URL", script)

    def test_compose_uses_container_reachable_local_endpoints(self) -> None:
        compose = self.read("docker-compose.yml")
        self.assertIn("http://host.docker.internal:54321", compose)
        self.assertIn("http://127.0.0.1:54321/auth/v1", compose)
        self.assertIn("NEXT_PUBLIC_API_ORIGIN", compose)

    def test_staging_runner_is_environment_scoped(self) -> None:
        script = self.read("scripts/staging.sh")
        self.assertIn('RAILWAY_STAGING_ENVIRONMENT:-staging', script)
        self.assertIn('"$ROOT/scripts/deploy.sh" api "$ENVIRONMENT"', script)
        self.assertIn('"$ROOT/scripts/deploy.sh" web "$ENVIRONMENT"', script)

    def test_staging_auth_redirects_are_exact_and_hosted(self) -> None:
        config = self.read("ops/staging/supabase/config.toml")
        self.assertIn('site_url = "https://web-staging-f7bf.up.railway.app"', config)
        self.assertNotIn("localhost", config)
        self.assertNotIn("127.0.0.1", config)

    def test_local_generated_files_and_staging_secrets_are_ignored(self) -> None:
        gitignore = self.read(".gitignore")
        self.assertIn(".local-runtime/", gitignore)
        self.assertIn(".env.staging.local", gitignore)
        self.assertIn(".env.local", gitignore)

    def test_deferred_storage_setup_can_be_reapplied(self) -> None:
        sql = self.read("supabase/deferred/book_sources_storage.sql")
        for operation in ("select", "insert", "update", "delete"):
            policy = f'"book_sources_owner_{operation}"'
            self.assertIn(f"drop policy if exists {policy}", sql)
            self.assertIn(f"create policy {policy}", sql)

    def test_production_clone_is_explicit_and_keeps_local_runtime_isolated(self) -> None:
        script = self.read("scripts/clone_prod_to_local.sh")
        self.assertIn('[[ "${1:-}" == "--yes"', script)
        self.assertIn('PRODUCTION_ENVIRONMENT="${RAILWAY_PRODUCTION_ENVIRONMENT:-production}"', script)
        self.assertIn('[[ "$local_supabase_url" == "http://127.0.0.1:54321" ]]', script)
        self.assertIn("bounded, resumable batches", script)
        self.assertIn("order by ctid limit $batch_size offset $offset", script)
        self.assertIn("set session_replication_role = replica", script)
        self.assertNotIn("usuulfckhbeypjxwjpfn", script)

    def test_production_clone_does_not_copy_auth_secrets(self) -> None:
        script = self.read("scripts/clone_prod_to_local.sh")
        self.assertNotIn("--schema=auth", script)
        self.assertIn("local-library@study-partner.test", script)
        self.assertIn("security add-generic-password", script)
        self.assertIn("auth/v1/admin/users", script)

    def test_production_clone_verifies_all_three_data_planes(self) -> None:
        script = self.read("scripts/clone_prod_to_local.sh")
        self.assertIn("canonical or derived database counts differ", script)
        self.assertIn("local Storage metadata does not match", script)
        self.assertIn("video media hash mismatch", script)
        self.assertIn('[[ "$book_count" == "$expected_document_count" ]]', script)
        self.assertIn('[[ "$video_count" == "$expected_video_count" ]]', script)
        self.assertIn("PostgreSQL major versions must match", script)

    def test_curated_staging_manifest_is_small_and_mixed(self) -> None:
        import json

        manifest = json.loads(self.read("ops/staging/curated_documents.json"))
        self.assertEqual(len(manifest), 6)
        self.assertEqual(len({item["id"] for item in manifest}), 6)
        self.assertTrue(all(item["reason"] for item in manifest))

    def test_staging_clone_is_explicit_and_environment_locked(self) -> None:
        script = self.read("scripts/clone_prod_to_staging.sh")
        self.assertIn('[[ "${1:-}" == "--yes"', script)
        self.assertIn('[[ "$SOURCE_ENVIRONMENT" == "production" ]]', script)
        self.assertIn('[[ "$TARGET_ENVIRONMENT" == "staging" ]]', script)
        self.assertIn('TARGET_PROJECT_REF="xtkbcireogbjjiuruvzp"', script)
        self.assertIn("source and target unexpectedly resolve to the same service", script)

    def test_staging_clone_preserves_free_plan_headroom_and_auth_isolation(self) -> None:
        script = self.read("scripts/clone_prod_to_staging.sh")
        self.assertIn("MAX_DATABASE_BYTES=$((350 * 1024 * 1024))", script)
        self.assertIn("staging-library@study-partner.test", script)
        self.assertIn("security add-generic-password", script)
        self.assertNotIn("--schema=auth", script)
        self.assertIn("cards_automation_eligible_at = null", script)

    def test_staging_clone_pauses_worker_and_verifies_media_and_notification(self) -> None:
        script = self.read("scripts/clone_prod_to_staging.sh")
        self.assertIn("us-west=0", script)
        self.assertGreaterEqual(script.count("us-west=1"), 2)
        self.assertIn("sha256sum -c", script)
        self.assertIn("staging video volume inventory differs", script)
        self.assertIn('[[ "$notification_href" == "/decks?review=today" ]]', script)


if __name__ == "__main__":
    unittest.main()
