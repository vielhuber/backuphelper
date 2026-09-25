<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

final readonly class Source
{
    /**
     * @param list<string> $skip
     * @param list<string> $exclude
     */
    private function __construct(
        public SourceType $type,
        public ?string $path,
        public ?string $name,
        public ?string $command,
        public ?string $check,
        public ?string $env,
        public array $skip,
        public array $exclude
    ) {}

    /**
     * Validate one entry of a job's sources list.
     *
     * @throws BackupException
     */
    public static function fromArray(mixed $value, string $label): self
    {
        if (!is_array($value)) {
            throw BackupException::invalidConfig($label . ' must be a mapping.');
        }
        $type = SourceType::tryFrom((string) ($value['type'] ?? ''));
        if ($type === null) {
            throw BackupException::invalidConfig($label . ' needs type path, git, command or ftpsh.');
        }
        $source = new self(
            type: $type,
            path: self::optionalString(value: $value['path'] ?? null, label: $label . '.path'),
            name: self::optionalString(value: $value['name'] ?? null, label: $label . '.name'),
            command: self::optionalString(value: $value['command'] ?? null, label: $label . '.command'),
            check: self::optionalString(value: $value['check'] ?? null, label: $label . '.check'),
            env: self::optionalString(value: $value['env'] ?? null, label: $label . '.env'),
            skip: self::stringList(value: $value['skip'] ?? [], label: $label . '.skip'),
            exclude: self::stringList(value: $value['exclude'] ?? [], label: $label . '.exclude')
        );
        $required = match ($type) {
            SourceType::Path, SourceType::Git => ['path' => $source->path],
            SourceType::Command => ['name' => $source->name, 'command' => $source->command],
            SourceType::Ftpsh => ['name' => $source->name, 'env' => $source->env, 'path' => $source->path]
        };
        foreach ($required as $key => $field) {
            if ($field === null) {
                throw BackupException::invalidConfig($label . ' (' . $type->value . ') needs ' . $key . '.');
            }
        }
        if ($source->name !== null && ($source->name === '' || str_contains($source->name, '/'))) {
            throw BackupException::invalidConfig($label . '.name must be a file name without slashes.');
        }
        if (in_array($type, [SourceType::Path, SourceType::Git], true) && !str_starts_with($source->path, '/')) {
            throw BackupException::invalidConfig($label . '.path must be absolute.');
        }
        return $source;
    }

    /**
     * Accept null or a string, so omitted keys stay null.
     *
     * @throws BackupException
     */
    public static function optionalString(mixed $value, string $label): ?string
    {
        if ($value !== null && !is_string($value)) {
            throw BackupException::invalidConfig($label . ' must be a string.');
        }
        return $value;
    }

    /**
     * Accept a list of non-empty strings (skip and exclude patterns).
     *
     * @return list<string>
     * @throws BackupException
     */
    public static function stringList(mixed $value, string $label): array
    {
        if (!is_array($value) || !array_is_list($value) || array_filter($value, fn($item) => !is_string($item) || $item === '') !== []) {
            throw BackupException::invalidConfig($label . ' must be a list of strings.');
        }
        return $value;
    }
}
