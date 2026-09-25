<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

use Exception;

final class BackupException extends Exception
{
    /**
     * Raised while reading the yaml config, before any job runs.
     */
    public static function invalidConfig(string $reason): self
    {
        return new self('Invalid config: ' . $reason);
    }

    /**
     * Raised when a source command, ftpsh or tar exits unsuccessfully.
     */
    public static function commandFailed(string $label, int $status): self
    {
        return new self($label . ' failed with exit status ' . $status . '.');
    }

    /**
     * Raised when command output lacks the configured completion marker.
     */
    public static function checkFailed(string $name, string $pattern): self
    {
        return new self($name . ' does not match the check ' . $pattern . '.');
    }
}
