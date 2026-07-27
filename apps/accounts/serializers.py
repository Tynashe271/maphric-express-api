from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name',
                 'phone_number', 'address', 'profile_picture', 'email_verified', 'is_staff']
        read_only_fields = ['email_verified', 'is_staff']

class RegisterSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'phone_number', 'password', 'password2', 'first_name', 'last_name']

    def validate_phone_number(self, value):
        normalized = ''.join(character for character in value if character.isdigit() or character == '+')
        if len(normalized.replace('+', '')) < 9:
            raise serializers.ValidationError("Enter a valid phone number.")
        if User.objects.filter(phone_number=normalized).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return normalized

    def validate_email(self, value):
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("An account with this email already exists. Please sign in.")
        return normalized

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
