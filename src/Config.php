<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

final readonly class Config
{
    /**
     * @param list<Job> $jobs
     */
    public function __construct(
        public string $ftpsh,
        public array $jobs
    ) {}
}
