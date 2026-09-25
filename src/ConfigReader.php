<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

use Symfony\Component\Yaml\Exception\ParseException;
use Symfony\Component\Yaml\Yaml;

final class ConfigReader
{
    public const DEFAULT_KEEP = 7;
    public const DEFAULT_INTERVAL = 0;
    public const DEFAULT_FTPSH = 'ftpsh';

    private string $file;

    /**
     * Path of the yaml config; relative ftpsh env files are resolved by ftpsh itself.
     */
    public function file(string $file): self
    {
        $this->file = $file;
        return $this;
    }

    /**
     * Read the ftpsh binary; jobs inherit target, keep, interval and exclude from the top level.
     *
     * @throws BackupException
     */
    public function read(): Config
    {
        try {
            $config = Yaml::parseFile($this->file);
        } catch (ParseException $exception) {
            throw BackupException::invalidConfig($exception->getMessage());
        }
        if (!is_array($config) || !is_array($config['jobs'] ?? null) || $config['jobs'] === []) {
            throw BackupException::invalidConfig('jobs must be a non-empty mapping.');
        }
        $jobs = [];
        foreach ($config['jobs'] as $name => $job) {
            $jobs[] = $this->job(name: (string) $name, job: $job, defaults: $config);
        }
        return new Config(
            ftpsh: Source::optionalString(value: $config['ftpsh'] ?? null, label: 'ftpsh') ?? self::DEFAULT_FTPSH,
            jobs: $jobs
        );
    }

    /**
     * Merge one job with the top-level defaults; exclude patterns of both levels apply.
     *
     * @param array<string, mixed> $defaults
     * @throws BackupException
     */
    private function job(string $name, mixed $job, array $defaults): Job
    {
        if (!preg_match('/^[A-Za-z0-9][A-Za-z0-9_.-]*$/', $name) || !is_array($job)) {
            throw BackupException::invalidConfig('jobs.' . $name . ' needs a simple name and a mapping.');
        }
        $target = Source::optionalString(value: $job['target'] ?? ($defaults['target'] ?? null), label: $name . '.target');
        if ($target === null || !str_starts_with($target, '/')) {
            throw BackupException::invalidConfig($name . ' needs an absolute target.');
        }
        $keep = $job['keep'] ?? ($defaults['keep'] ?? self::DEFAULT_KEEP);
        $interval = $job['interval'] ?? ($defaults['interval'] ?? self::DEFAULT_INTERVAL);
        if (!is_int($keep) || $keep < 1 || !is_int($interval) || $interval < 0) {
            throw BackupException::invalidConfig($name . ' needs keep >= 1 and interval >= 0 (hours).');
        }
        if (!is_array($job['sources'] ?? null) || !array_is_list($job['sources']) || $job['sources'] === []) {
            throw BackupException::invalidConfig($name . '.sources must be a non-empty list.');
        }
        $sources = [];
        foreach ($job['sources'] as $index => $source) {
            $sources[] = Source::fromArray(value: $source, label: $name . '.sources[' . $index . ']');
        }
        return new Job(
            name: $name,
            target: rtrim($target, '/'),
            keep: $keep,
            interval: $interval,
            exclude: [
                ...Source::stringList(value: $defaults['exclude'] ?? [], label: 'exclude'),
                ...Source::stringList(value: $job['exclude'] ?? [], label: $name . '.exclude')
            ],
            sources: $sources
        );
    }
}
