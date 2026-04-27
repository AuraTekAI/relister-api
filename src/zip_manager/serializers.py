from rest_framework import serializers
from .models import ZipFile


class ZipFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZipFile
        fields = ['id', 'filename', 'base_name', 'version', 's3_key', 'uploaded_at', 'updated_at']
        read_only_fields = ['id', 'filename', 'base_name', 'version', 's3_key', 'uploaded_at', 'updated_at']


class ZipFileUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        if not value.name.endswith('.zip'):
            raise serializers.ValidationError(
                "Invalid filename format. Use: <name>_v<number>.zip "
                "(example: assets_v1.zip)"
            )
        try:
            ZipFile.parse_filename(value.name)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return value
