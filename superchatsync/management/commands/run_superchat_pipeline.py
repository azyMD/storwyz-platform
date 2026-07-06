from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from superchatsync.models import SuperchatSyncRun


class Command(BaseCommand):
    help = "Run full Superchat post-processing pipeline for one sync run."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        run_id = options["run_id"]
        force = options.get("force")

        try:
            run = SuperchatSyncRun.objects.get(run_id=run_id)
        except SuperchatSyncRun.DoesNotExist:
            raise CommandError(f"Run ID not found: {run_id}")

        self.stdout.write(self.style.WARNING(
            f"Starting Superchat pipeline for run_id={run_id}, force={force}"
        ))

        run.status = "postprocessing"
        run.current_conversation_id = None
        run.notes = (run.notes or "") + f"\nPost-processing pipeline started at {timezone.now().isoformat()}."
        run.save(update_fields=["status", "current_conversation_id", "notes", "updated_at"])

        steps = [
            ("process_superchat_archives", {"run_id": run_id, "force": force}),
            ("extract_superchat_text", {"run_id": run_id, "force": force}),
            ("parse_superchat_texts", {"run_id": run_id, "force": force}),
            ("enrich_conversations_products", {"run_id": run_id, "force": force}),
        ]

        try:
            for command_name, kwargs in steps:
                run.refresh_from_db()

                if run.stop_requested or run.status == "stopping":
                    run.status = "stopped"
                    run.finished_at = timezone.now()
                    run.notes = (run.notes or "") + f"\nPipeline stopped before step: {command_name}."
                    run.save(update_fields=["status", "finished_at", "notes", "updated_at"])
                    self.stdout.write(self.style.WARNING("Pipeline stopped by user."))
                    return

                self.stdout.write(self.style.WARNING(f"Running step: {command_name}"))

                run.notes = (run.notes or "") + f"\nRunning pipeline step: {command_name}"
                run.save(update_fields=["notes", "updated_at"])

                call_command(command_name, **kwargs)

                self.stdout.write(self.style.SUCCESS(f"Completed step: {command_name}"))

            run.status = "postprocessed"
            run.finished_at = timezone.now()
            run.current_conversation_id = None
            run.notes = (run.notes or "") + f"\nPost-processing pipeline completed at {timezone.now().isoformat()}."
            run.save(update_fields=["status", "finished_at", "current_conversation_id", "notes", "updated_at"])

            self.stdout.write(self.style.SUCCESS("Superchat pipeline completed successfully."))

        except Exception as e:
            run.status = "failed"
            run.finished_at = timezone.now()
            run.current_conversation_id = None
            run.notes = (run.notes or "") + f"\nPost-processing pipeline failed: {str(e)}"
            run.save(update_fields=["status", "finished_at", "current_conversation_id", "notes", "updated_at"])

            self.stdout.write(self.style.ERROR(f"Pipeline failed: {e}"))
            raise
