from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from ...demo import DEMO_GROUP


class Command(BaseCommand):
    help = "Create (or update) a non-staff user locked into demo mode: it only ever sees the demo database."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--password", help="Prompted for when omitted.")

    def handle(self, *args, username, password=None, **options):
        if not password:
            password = getpass("Contraseña: ")
            if password != getpass("Repetir contraseña: "):
                raise CommandError("Las contraseñas no coinciden.")
        if not password:
            raise CommandError("La contraseña no puede estar vacía.")
        user, creado = get_user_model().objects.get_or_create(username=username)
        if user.is_staff or user.is_superuser:
            raise CommandError(f"{username} es staff/superusuario: no se puede convertir en usuario demo.")
        user.set_password(password)
        user.save()
        user.groups.add(Group.objects.get_or_create(name=DEMO_GROUP)[0])
        self.stdout.write(f"Usuario demo {username} {'creado' if creado else 'actualizado'}.")
